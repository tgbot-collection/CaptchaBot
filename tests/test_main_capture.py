from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main
from spamdet import ModelStatus, SpamLearningConfig, sample_from_admin_forward
from spamdet.config import ConfigError
from spamdet.d1 import ManualSpamImportResult


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
        old_service, old_config = main.spam_capture_service, main.spamdet_config
        capture = RecordingCapture()
        main.spam_capture_service = capture
        main.spamdet_config = SpamLearningConfig(enabled=True, record_groups=frozenset({-100123}))
        try:
            with patch.object(
                main.app,
                "get_chat",
                new=AsyncMock(return_value=SimpleNamespace(bio="个人简介中的联系方式 @example")),
            ):
                first = FakeMessage()
                self.assertFalse(await main.group_message_preprocess(main.app, first))
                second = FakeMessage()
                second.chat.id = -999999
                self.assertFalse(await main.group_message_preprocess(main.app, second))
            self.assertEqual(len(capture.samples), 1)
            self.assertEqual((capture.samples[0].source, capture.samples[0].status, capture.samples[0].label),
                             ("group", "pending", None))
            self.assertFalse(first.deleted)
            self.assertEqual(capture.samples[0].user.bio, "个人简介中的联系方式 @example")
            self.assertEqual(capture.samples[0].user.bio_state, "present")
        finally:
            main.spam_capture_service, main.spamdet_config = old_service, old_config


class AdminForwardSpamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_config = main.spamdet_config
        self.original_d1 = main.spam_d1_client

    async def asyncTearDown(self) -> None:
        main.spamdet_config = self.original_config
        main.spam_d1_client = self.original_d1

    @staticmethod
    def forwarded_message(actor_id: int = 123):
        message = FakeMessage()
        message.chat.id = actor_id
        message.from_user.id = actor_id
        message.from_user.username = "admin"
        message.text = "原始 Spam 内容🚀"
        message.forward_origin = SimpleNamespace(
            type="user",
            date=datetime(2026, 11, 1, 1, 2, 3, tzinfo=timezone.utc),
            sender_user=SimpleNamespace(
                id=999,
                username="origin",
                first_name="原作者",
                last_name="姓名",
                is_premium=True,
            ),
            sender_chat=None,
            chat=None,
            message_id=None,
        )
        message.replies = []

        async def reply_text(text: str) -> None:
            message.replies.append(text)

        message.reply_text = reply_text
        return message

    async def test_admin_forward_is_reviewed_spam_and_uses_origin_user(self) -> None:
        store = SimpleNamespace(
            import_forwarded_spam=AsyncMock(return_value=ManualSpamImportResult(id=77, created=True))
        )
        main.spamdet_config = SpamLearningConfig(admin_id=123)
        main.spam_d1_client = store
        message = self.forwarded_message()

        await main.admin_forwarded_spam_handler(SimpleNamespace(), message)

        sample, reviewer = store.import_forwarded_spam.await_args.args
        self.assertEqual(reviewer, 123)
        self.assertEqual((sample.source, sample.status, sample.label), ("manual", "reviewed", "spam"))
        self.assertEqual(sample.message.text, "原始 Spam 内容🚀")
        self.assertEqual(sample.user.id, 999)
        self.assertEqual(sample.user.username, "origin")
        self.assertNotEqual(sample.user.id, message.from_user.id)
        self.assertEqual(message.replies, ["Spam 样本已保存，ID=77。"])

    async def test_non_admin_forward_is_rejected_without_write(self) -> None:
        store = SimpleNamespace(import_forwarded_spam=AsyncMock())
        main.spamdet_config = SpamLearningConfig(admin_id=123)
        main.spam_d1_client = store
        message = self.forwarded_message(actor_id=456)

        await main.admin_forwarded_spam_handler(SimpleNamespace(), message)

        store.import_forwarded_spam.assert_not_awaited()
        self.assertEqual(message.replies, ["你没有提交 Spam 样本的权限。"])

    async def test_admin_cannot_mark_own_forward_origin_as_spam(self) -> None:
        store = SimpleNamespace(import_forwarded_spam=AsyncMock())
        main.spamdet_config = SpamLearningConfig(admin_id=123)
        main.spam_d1_client = store
        message = self.forwarded_message()
        message.forward_origin.sender_user.id = 123

        await main.admin_forwarded_spam_handler(SimpleNamespace(), message)

        store.import_forwarded_spam.assert_not_awaited()
        self.assertEqual(message.replies, ["不能将管理员本人作为 Spam 原作者，消息未保存。"])

    def test_hidden_and_channel_origins_preserve_available_author_fields(self) -> None:
        hidden = self.forwarded_message()
        hidden.forward_origin = SimpleNamespace(
            type="hidden_user", date=hidden.date, sender_user_name="隐藏作者", sender_user=None,
            sender_chat=None, chat=None, message_id=None,
        )
        hidden_sample = sample_from_admin_forward(hidden)
        self.assertEqual(hidden_sample.user.first_name, "隐藏作者")
        self.assertIsNone(hidden_sample.user.id)

        channel = self.forwarded_message()
        channel.caption = "频道广告 caption"
        channel.text = None
        channel.caption_entities = []
        channel.forward_origin = SimpleNamespace(
            type="channel", date=channel.date, sender_user=None, sender_chat=None,
            chat=SimpleNamespace(id=-100999, username="source_channel", title="来源频道"),
            message_id=88, author_signature="频道作者",
        )
        channel_sample = sample_from_admin_forward(channel)
        self.assertEqual((channel_sample.message.chat_id, channel_sample.message.message_id), (-100999, 88))
        self.assertEqual(channel_sample.message.caption, "频道广告 caption")
        self.assertEqual(channel_sample.user.username, "source_channel")
        self.assertEqual((channel_sample.user.first_name, channel_sample.user.last_name), ("来源频道", "频道作者"))

    def test_checklist_is_flattened_with_utf16_entity_offsets(self) -> None:
        message = self.forwarded_message()
        message.text = None
        message.caption = None
        message.media = "checklist"
        message.checklist = SimpleNamespace(
            title="标题😀",
            entities=[SimpleNamespace(type="bold", offset=0, length=4)],
            tasks=[
                SimpleNamespace(
                    text="第一项",
                    entities=[SimpleNamespace(type="bold", offset=0, length=3)],
                ),
                SimpleNamespace(
                    text="https://t.me/+example",
                    entities=[SimpleNamespace(type="url", offset=0, length=21)],
                ),
            ],
        )

        sample = sample_from_admin_forward(message)

        self.assertEqual(sample.message.text, "标题😀\n第一项\nhttps://t.me/+example")
        self.assertEqual(sample.message.media_type, "checklist")
        self.assertEqual([entity.offset for entity in sample.message.entities], [0, 5, 9])
        self.assertEqual([entity.type for entity in sample.message.entities], ["bold", "bold", "url"])

    def test_other_structured_media_text_is_extracted(self) -> None:
        formatted = lambda text: SimpleNamespace(text=text, entities=[])
        cases = [
            (
                "poll",
                SimpleNamespace(
                    question=formatted("投票问题"),
                    options=[SimpleNamespace(text=formatted("选项一")), SimpleNamespace(text=formatted("选项二"))],
                    explanation=formatted("答案说明"),
                    description=formatted("投票描述"),
                ),
                "投票问题\n选项一\n选项二\n答案说明\n投票描述",
            ),
            ("contact", SimpleNamespace(first_name="联系人", last_name="姓名", phone_number="+123", vcard="VCARD"),
             "联系人\n姓名\n+123"),
            ("venue", SimpleNamespace(title="场所名称", address="场所地址"), "场所名称\n场所地址"),
            ("location", SimpleNamespace(address="位置文字地址"), "位置文字地址"),
            ("game", SimpleNamespace(title="游戏标题", description="游戏描述", short_name="game_name"),
             "游戏标题\n游戏描述\ngame_name"),
            (
                "invoice",
                SimpleNamespace(
                    title="商品标题", description="商品描述", start_parameter="start",
                    prices=[SimpleNamespace(label="商品价格"), SimpleNamespace(label="运费")],
                ),
                "商品标题\n商品描述\nstart\n商品价格\n运费",
            ),
            (
                "giveaway",
                SimpleNamespace(description="赠品描述", chats=[SimpleNamespace(title="参与频道", username=None)]),
                "赠品描述\n参与频道",
            ),
            (
                "giveaway_winners",
                SimpleNamespace(prize_description="获奖说明", chat=SimpleNamespace(title="主办频道", username=None)),
                "获奖说明\n主办频道",
            ),
            ("story", SimpleNamespace(caption="Story caption", caption_entities=[]), "Story caption"),
            (
                "web_page",
                SimpleNamespace(
                    url="https://example.com", site_name="站点", title="网页标题",
                    description="网页描述", author="网页作者",
                ),
                "https://example.com\n站点\n网页标题\n网页描述\n网页作者",
            ),
            ("audio", SimpleNamespace(performer="歌手", title="歌曲", file_name="audio.mp3"),
             "歌手\n歌曲\naudio.mp3"),
            ("document", SimpleNamespace(file_name="广告文档.pdf"), "广告文档.pdf"),
            ("video", SimpleNamespace(file_name="广告视频.mp4"), "广告视频.mp4"),
            ("animation", SimpleNamespace(file_name="广告动画.gif"), "广告动画.gif"),
            ("sticker", SimpleNamespace(emoji="🚀", set_name="spam_set", file_name="sticker.webp"),
             "🚀\nspam_set\nsticker.webp"),
        ]

        for attribute, content, expected in cases:
            with self.subTest(attribute=attribute):
                message = self.forwarded_message()
                message.text = None
                message.caption = None
                setattr(message, attribute, content)

                sample = sample_from_admin_forward(message)

                self.assertEqual(sample.message.text, expected)

    def test_binary_only_media_without_text_is_rejected(self) -> None:
        message = self.forwarded_message()
        message.text = None
        message.caption = None
        message.photo = SimpleNamespace(file_id="not-persisted")

        with self.assertRaisesRegex(ValueError, "可提取的文字"):
            sample_from_admin_forward(message)


class ModelHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original_service = main.spam_inference_service
        self.original_config = main.spamdet_config

    async def asyncTearDown(self) -> None:
        main.spam_inference_service = self.original_service
        main.spamdet_config = self.original_config

    async def test_only_configured_admin_can_view_model_status(self) -> None:
        class FakeInference:
            status = ModelStatus(True, "similarity", "seed-v1")

        class CommandMessage:
            def __init__(self, actor_id: int) -> None:
                self.from_user = SimpleNamespace(id=actor_id)
                self.replies: list[str] = []

            async def reply_text(self, text: str) -> None:
                self.replies.append(text)

        main.spamdet_config = SpamLearningConfig(admin_id=123)
        main.spam_inference_service = FakeInference()
        admin_message = CommandMessage(123)
        other_message = CommandMessage(456)

        await main.model_handler(SimpleNamespace(), admin_message)
        await main.model_handler(SimpleNamespace(), other_message)

        self.assertIn("版本: seed-v1", admin_message.replies[0])
        self.assertEqual(other_message.replies, ["你没有查看模型状态的权限。"])


if __name__ == "__main__":
    unittest.main()
