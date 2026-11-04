from __future__ import annotations

from types import SimpleNamespace
import unittest

from spam_learning.d1 import D1APIError, ReviewOutcome, SampleStats
from spam_learning.review import ReviewService, render_pending_notification
from spam_learning.schemas import MessageSnapshot, ModelSuggestion, SpamSample, UserSnapshot


ADMIN_ID = 12345


def make_sample(*, model: ModelSuggestion | None = None) -> SpamSample:
    return SpamSample(
        source="rule",
        message=MessageSnapshot(
            chat_id=-100123,
            message_id=42,
            date="2026-11-04T08:00:00Z",
            text="<b>原始&消息</b>🚀",
            caption=None,
        ),
        user=UserSnapshot(
            id=456,
            username="sender<name>",
            first_name="张&三",
            bio="bio <script>",
        ),
        matched_rule="blacklist_message:收米",
        model=model,
    )


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return SimpleNamespace(id=88)


class FakeStore:
    def __init__(self) -> None:
        self.review_calls = []
        self.outcome = ReviewOutcome.CHANGED
        self.existing_label = "spam"
        self.status = "pending"
        self.review_message_id = None
        self.error: Exception | None = None
        self.review_messages = []

    async def set_review_message(self, sample_id: int, chat_id: int, message_id: int) -> None:
        self.review_messages.append((sample_id, chat_id, message_id))

    async def review(self, sample_id: int, label: str, admin_id: int) -> ReviewOutcome:
        self.review_calls.append((sample_id, label, admin_id))
        if self.error is not None:
            raise self.error
        return self.outcome

    async def get(self, sample_id: int):
        return SimpleNamespace(
            label=self.existing_label,
            status=self.status,
            review_message_id=self.review_message_id,
        )

    async def stats(self) -> SampleStats:
        return SampleStats(total=5, pending=2, reviewed=3, spam=2, ham=1)


class FakeMessage:
    def __init__(self, text: str = "待审核通知") -> None:
        self.text = text
        self.edits = []
        self.replies = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeCallback:
    def __init__(self, data: str, *, actor_id: int = ADMIN_ID) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=actor_id)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class ReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_is_escaped_and_has_exactly_two_buttons(self) -> None:
        telegram = FakeTelegram()
        store = FakeStore()
        service = ReviewService(telegram, store, admin_id=ADMIN_ID)

        await service.notify_pending(7, make_sample())

        self.assertEqual(len(telegram.sent), 1)
        args, kwargs = telegram.sent[0]
        self.assertEqual(args[0], ADMIN_ID)
        self.assertIn("&lt;b&gt;原始&amp;消息&lt;/b&gt;", args[1])
        buttons = kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(buttons), 1)
        self.assertEqual([button.text for button in buttons[0]], ["✅ Spam", "❌ Not Spam"])
        self.assertEqual([button.callback_data for button in buttons[0]], ["sd:7:s", "sd:7:h"])
        self.assertEqual(store.review_messages, [(7, ADMIN_ID, 88)])

    async def test_unknown_is_not_rendered_as_ham(self) -> None:
        model = ModelSuggestion(version="v0", kind="similarity", decision="unknown", score=0.1)
        rendered = render_pending_notification(1, make_sample(model=model))
        self.assertIn("unknown（无法判断，不等于 Ham）", rendered)
        self.assertIn("相似度=0.1000（不是概率）", rendered)
        self.assertNotIn("score=", rendered)

    async def test_unavailable_model_is_explained_without_fake_version(self) -> None:
        model = ModelSuggestion(
            version=None,
            kind="similarity",
            decision="unavailable",
            score=None,
        )
        rendered = render_pending_notification(1, make_sample(model=model))
        self.assertIn("unavailable（模型不可用，不影响保存和人工审核）", rendered)
        self.assertIn("相似度=无（不是概率）", rendered)
        self.assertIn("版本=无", rendered)

    async def test_existing_notification_is_not_sent_again(self) -> None:
        telegram = FakeTelegram()
        store = FakeStore()
        store.review_message_id = 77
        service = ReviewService(telegram, store, admin_id=ADMIN_ID)

        await service.notify_pending(7, make_sample())

        self.assertEqual(telegram.sent, [])
        self.assertEqual(store.review_messages, [])

    async def test_escaped_notification_stays_within_telegram_limit(self) -> None:
        original = make_sample()
        sample = SpamSample(
            source="rule",
            message=MessageSnapshot(
                chat_id=-100123,
                message_id=42,
                date=None,
                text='<&"' * 5000,
                caption=None,
            ),
            user=original.user,
            matched_rule=original.matched_rule,
        )
        rendered = render_pending_notification(1, sample)
        self.assertLessEqual(len(rendered), 4096)
        self.assertIn("…", rendered)

    async def test_authorized_callback_reviews_then_edits_notification(self) -> None:
        store = FakeStore()
        callback = FakeCallback("sd:7:h")
        service = ReviewService(FakeTelegram(), store, admin_id=ADMIN_ID)

        await service.handle_callback(callback)

        self.assertEqual(store.review_calls, [(7, "ham", ADMIN_ID)])
        self.assertEqual(callback.answers[0][0], "正在保存审核结果…")
        self.assertIn("审核结果：Not Spam", callback.message.edits[0][0])
        self.assertIsNone(callback.message.edits[0][1]["reply_markup"])

    async def test_unauthorized_callback_never_touches_store(self) -> None:
        store = FakeStore()
        callback = FakeCallback("sd:7:s", actor_id=999)
        service = ReviewService(FakeTelegram(), store, admin_id=ADMIN_ID)

        await service.handle_callback(callback)

        self.assertEqual(store.review_calls, [])
        self.assertTrue(callback.answers[0][1]["show_alert"])
        self.assertEqual(callback.message.edits, [])

    async def test_repeated_callback_displays_existing_label_without_overwrite(self) -> None:
        store = FakeStore()
        store.outcome = ReviewOutcome.ALREADY_REVIEWED
        store.existing_label = "spam"
        callback = FakeCallback("sd:7:h")
        service = ReviewService(FakeTelegram(), store, admin_id=ADMIN_ID)

        await service.handle_callback(callback)

        self.assertIn("审核结果：Spam", callback.message.edits[0][0])
        self.assertIn("未覆盖原标签", callback.message.edits[0][0])

    async def test_d1_failure_keeps_original_notification_and_reports_retry(self) -> None:
        store = FakeStore()
        store.error = D1APIError("failed")
        callback = FakeCallback("sd:7:s")
        service = ReviewService(FakeTelegram(), store, admin_id=ADMIN_ID)

        await service.handle_callback(callback)

        self.assertEqual(callback.message.edits, [])
        self.assertIn("请稍后重试", callback.message.replies[0][0])

    async def test_dataset_stats_contains_only_aggregates(self) -> None:
        service = ReviewService(FakeTelegram(), FakeStore(), admin_id=ADMIN_ID)
        text = await service.dataset_stats_text()
        self.assertEqual(text, "Spam 数据集统计\ntotal: 5\npending: 2\nreviewed: 3\nspam: 2\nham: 1")


if __name__ == "__main__":
    unittest.main()
