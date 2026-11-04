from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from spam_learning.capture import CaptureService
from spam_learning.schemas import MessageSnapshot, SpamSample, UserSnapshot


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


class CaptureServiceTests(unittest.IsolatedAsyncioTestCase):
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
            user=UserSnapshot(id=original.user.id, bio="已有 Bio"),
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


if __name__ == "__main__":
    unittest.main()
