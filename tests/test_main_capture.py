from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main


class RecordingCapture:
    def __init__(self) -> None:
        self.samples = []

    def submit(self, sample) -> bool:
        self.samples.append(sample)
        return True


class FakeMessage:
    def __init__(self, *, delete_error: Exception | None = None) -> None:
        self.id = 42
        self.date = datetime(2026, 11, 4, tzinfo=timezone.utc)
        self.text = "删除前原文🚀"
        self.caption = None
        self.entities = []
        self.caption_entities = []
        self.media = None
        self.chat = SimpleNamespace(id=-100123)
        self.from_user = SimpleNamespace(
            id=123,
            username="sender",
            first_name="First",
            last_name=None,
            is_premium=None,
        )
        self.via_bot = None
        self.reply_markup = None
        self.guest_bot_caller_user = None
        self.guest_bot_caller_chat = None
        self.deleted = False
        self.delete_error = delete_error

    async def delete(self) -> None:
        self.deleted = True
        self.text = "删除时对象已变更"
        if self.delete_error is not None:
            raise self.delete_error


class DeleteRuleMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_service = main.spam_capture_service

    async def asyncTearDown(self) -> None:
        main.spam_capture_service = self.original_service

    async def test_snapshot_is_taken_before_delete(self) -> None:
        capture = RecordingCapture()
        main.spam_capture_service = capture
        message = FakeMessage()

        await main.delete_rule_message(message, matched_rule="blacklist_message:收米")

        self.assertTrue(message.deleted)
        self.assertEqual(len(capture.samples), 1)
        self.assertEqual(capture.samples[0].message.text, "删除前原文🚀")

    async def test_delete_failure_is_propagated_after_capture_submit(self) -> None:
        capture = RecordingCapture()
        main.spam_capture_service = capture
        message = FakeMessage(delete_error=RuntimeError("delete failed"))

        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            await main.delete_rule_message(message, matched_rule="contact")

        self.assertEqual(len(capture.samples), 1)

    async def test_disabled_capture_keeps_plain_delete_path(self) -> None:
        main.spam_capture_service = None
        message = FakeMessage()

        await main.delete_rule_message(message, matched_rule="contact")

        self.assertTrue(message.deleted)


class KeywordHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_service = main.spam_capture_service

    async def asyncTearDown(self) -> None:
        main.spam_capture_service = self.original_service

    async def test_keyword_hit_deletes_and_enqueues_without_network_lookup(self) -> None:
        capture = RecordingCapture()
        main.spam_capture_service = capture
        message = FakeMessage()
        message.text = "今天收米"
        client = SimpleNamespace()

        with patch.dict("os.environ", {"BLACKLIST_MESSAGE": "收米"}, clear=False):
            result = await main.group_message_preprocess(client, message)

        self.assertTrue(result)
        self.assertTrue(message.deleted)
        self.assertEqual(len(capture.samples), 1)
        self.assertEqual(capture.samples[0].matched_rule, "blacklist_message:收米")


if __name__ == "__main__":
    unittest.main()
