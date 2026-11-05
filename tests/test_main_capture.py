from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main
from spam_learning import ModelStatus, SpamLearningConfig
from spam_learning.config import ConfigError


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
        self.forward_origin = None
        self.sticker = None
        self.contact = None
        self.document = None
        self.from_user.emoji_status = None

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


class GroupSelectionTests(unittest.TestCase):
    def test_allowlist_and_all_are_explicit(self) -> None:
        base = {"SPAM_LEARNING_ENABLED": "true", "SPAM_ADMIN_ID": "123",
                "D1_ACCOUNT_ID": "a", "D1_DATABASE_ID": "d", "D1_API_TOKEN": "t"}
        self.assertFalse(SpamLearningConfig.from_env(base).records_group(-1001))
        listed = SpamLearningConfig.from_env({**base, "SPAM_CAPTURE_GROUPS": "-1001,-2002"})
        self.assertTrue(listed.records_group(-1001))
        self.assertFalse(listed.records_group(-3003))
        self.assertTrue(SpamLearningConfig.from_env({**base, "SPAM_CAPTURE_GROUPS": "all"}).records_group(-3003))
        with self.assertRaises(ConfigError):
            SpamLearningConfig.from_env({**base, "SPAM_CAPTURE_GROUPS": "all,-1001"})


class GroupCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_normal_message_is_pending_and_other_group_is_not_recorded(self) -> None:
        old_service, old_config = main.spam_capture_service, main.spam_learning_config
        capture = RecordingCapture()
        main.spam_capture_service = capture
        main.spam_learning_config = SpamLearningConfig(enabled=True, record_groups=frozenset({-100123}))
        try:
            with patch.object(main.app, "get_chat", new=AsyncMock(return_value=SimpleNamespace(bio=None))):
                first = FakeMessage()
                self.assertFalse(await main.group_message_preprocess(main.app, first))
                second = FakeMessage()
                second.chat.id = -999999
                self.assertFalse(await main.group_message_preprocess(main.app, second))
            self.assertEqual(len(capture.samples), 1)
            self.assertEqual((capture.samples[0].source, capture.samples[0].status, capture.samples[0].label),
                             ("group", "pending", None))
            self.assertFalse(first.deleted)
        finally:
            main.spam_capture_service, main.spam_learning_config = old_service, old_config


class ModelHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_service = main.spam_inference_service
        self.original_config = main.spam_learning_config

    async def asyncTearDown(self) -> None:
        main.spam_inference_service = self.original_service
        main.spam_learning_config = self.original_config

    async def test_only_configured_admin_can_view_model_status(self) -> None:
        class FakeInference:
            status = ModelStatus(True, "similarity", "seed-v1")

        class CommandMessage:
            def __init__(self, actor_id: int) -> None:
                self.from_user = SimpleNamespace(id=actor_id)
                self.replies: list[str] = []

            async def reply_text(self, text: str) -> None:
                self.replies.append(text)

        main.spam_learning_config = SpamLearningConfig(admin_id=123)
        main.spam_inference_service = FakeInference()
        admin_message = CommandMessage(123)
        other_message = CommandMessage(456)

        await main.model_handler(SimpleNamespace(), admin_message)
        await main.model_handler(SimpleNamespace(), other_message)

        self.assertIn("版本: seed-v1", admin_message.replies[0])
        self.assertEqual(other_message.replies, ["你没有查看模型状态的权限。"])


if __name__ == "__main__":
    unittest.main()
