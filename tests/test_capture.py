from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from dataclasses import replace

from spamdet.capture import CaptureService
from spamdet.schemas import MessageSnapshot, SpamSample, UserSnapshot


def make_sample(message_id: int, *, user_id: int = 123) -> SpamSample:
    return SpamSample(
        source="rule",
        message=MessageSnapshot(
            chat_id=-100123,
            message_id=message_id,
            date="2026-11-04T08:00:00Z",
            text="原始 Unicode 消息🚀",
            caption=None,
        ),
        user=UserSnapshot(id=user_id),
        matched_rule="blacklist_message:收米",
    )


class FakeTelegram:
    def __init__(self, *, bio: str | None = "简介", delay: float = 0) -> None:
        self.bio = bio
        self.delay = delay
        self.calls = 0

    async def get_chat(self, user_id: int) -> SimpleNamespace:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return SimpleNamespace(bio=self.bio)


class RecordingStore:
    def __init__(self) -> None:
        self.samples: list[SpamSample] = []

    async def create_pending(self, sample: SpamSample) -> int:
        self.samples.append(sample)
        return len(self.samples)


class BlockingStore(RecordingStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def create_pending(self, sample: SpamSample) -> int:
        self.started.set()
        await self.release.wait()
        return await super().create_pending(sample)


class FailingStore(RecordingStore):
    async def create_pending(self, sample: SpamSample) -> int:
        raise RuntimeError("D1 failed")


class RecordingNotifier:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, SpamSample]] = []
        self.error = error

    async def notify_pending(self, sample_id: int, sample: SpamSample) -> None:
        self.calls.append((sample_id, sample))
        if self.error is not None:
            raise self.error


class RecordingInference:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[SpamSample] = []
        self.error = error

    async def enrich(self, sample: SpamSample) -> SpamSample:
        self.calls.append(sample)
        if self.error is not None:
            raise self.error
        from dataclasses import replace
        from spamdet.schemas import ModelSuggestion

        return replace(
            sample,
            model=ModelSuggestion(
                version="seed-v1",
                kind="similarity",
                decision="spam_like",
                score=0.9,
            ),
        )


class CaptureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_sample_writes_without_bio_lookup_or_push(self) -> None:
        telegram, store, notifier = FakeTelegram(), RecordingStore(), RecordingNotifier()
        service = CaptureService(telegram, store, notifier)
        service.start()
        try:
            self.assertTrue(service.submit(replace(make_sample(7), source="group", matched_rule=None)))
            await service.wait_until_idle()
        finally:
            await service.aclose()
        self.assertEqual(telegram.calls, 0)
        self.assertEqual(len(store.samples), 1)
        self.assertEqual(notifier.calls, [])

    async def test_prediction_is_persisted_and_sent_after_bio_enrichment(self) -> None:
        telegram = FakeTelegram(bio="简介")
        store = RecordingStore()
        notifier = RecordingNotifier()
        inference = RecordingInference()
        service = CaptureService(
            telegram,
            store,
            notifier,
            inference=inference,
            worker_count=1,
        )
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(inference.calls[0].user.bio, "简介")
        self.assertEqual(inference.calls[0].user.bio_state, "present")
        self.assertEqual(store.samples[0].model.decision, "spam_like")
        self.assertIs(notifier.calls[0][1], store.samples[0])

    async def test_prediction_failure_still_writes_unavailable_pending(self) -> None:
        store = RecordingStore()
        service = CaptureService(
            FakeTelegram(),
            store,
            inference=RecordingInference(error=RuntimeError("failed")),
            worker_count=1,
        )
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(len(store.samples), 1)
        self.assertEqual(store.samples[0].model.decision, "unavailable")
        self.assertIsNone(store.samples[0].model.version)

    async def test_notifies_only_after_pending_write_succeeds(self) -> None:
        telegram = FakeTelegram()
        store = RecordingStore()
        notifier = RecordingNotifier()
        service = CaptureService(telegram, store, notifier, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(len(store.samples), 1)
        self.assertEqual(notifier.calls, [(1, store.samples[0])])

    async def test_notification_failure_keeps_written_pending_sample(self) -> None:
        telegram = FakeTelegram()
        store = RecordingStore()
        notifier = RecordingNotifier(error=RuntimeError("send failed"))
        service = CaptureService(telegram, store, notifier, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(len(store.samples), 1)
        self.assertEqual(len(notifier.calls), 1)

    async def test_pending_write_failure_never_sends_notification(self) -> None:
        telegram = FakeTelegram()
        notifier = RecordingNotifier()
        service = CaptureService(telegram, FailingStore(), notifier, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(notifier.calls, [])

    async def test_enriches_bio_and_reuses_bounded_ttl_cache(self) -> None:
        telegram = FakeTelegram(bio="保留原始 Bio")
        store = RecordingStore()
        service = CaptureService(telegram, store, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            self.assertTrue(service.submit(make_sample(2)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(telegram.calls, 1)
        self.assertEqual([sample.user.bio for sample in store.samples], ["保留原始 Bio", "保留原始 Bio"])
        self.assertEqual([sample.user.bio_state for sample in store.samples], ["present", "present"])

    async def test_bio_timeout_still_writes_pending_sample(self) -> None:
        telegram = FakeTelegram(delay=0.05)
        store = RecordingStore()
        service = CaptureService(telegram, store, worker_count=1, bio_timeout=0.001)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(len(store.samples), 1)
        self.assertIsNone(store.samples[0].user.bio)
        self.assertEqual(store.samples[0].user.bio_state, "unavailable")

    async def test_full_queue_drops_without_blocking(self) -> None:
        telegram = FakeTelegram()
        store = BlockingStore()
        service = CaptureService(telegram, store, queue_size=1, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(make_sample(1)))
            await store.started.wait()
            self.assertTrue(service.submit(make_sample(2)))
            self.assertFalse(service.submit(make_sample(3)))
            store.release.set()
            await service.wait_until_idle()
        finally:
            store.release.set()
            await service.aclose()

        self.assertEqual(len(store.samples), 2)

    async def test_existing_bio_skips_telegram_request(self) -> None:
        telegram = FakeTelegram()
        store = RecordingStore()
        original = make_sample(1)
        sample = SpamSample(
            source=original.source,
            message=original.message,
            user=UserSnapshot(id=original.user.id, bio="已有 Bio", bio_state="present"),
            matched_rule=original.matched_rule,
        )
        service = CaptureService(telegram, store, worker_count=1)
        service.start()
        try:
            self.assertTrue(service.submit(sample))
            await service.wait_until_idle()
        finally:
            await service.aclose()

        self.assertEqual(telegram.calls, 0)
        self.assertEqual(store.samples[0].user.bio, "已有 Bio")
        self.assertEqual(store.samples[0].user.bio_state, "present")


if __name__ == "__main__":
    unittest.main()
