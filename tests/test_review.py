from __future__ import annotations

from types import SimpleNamespace
import unittest

from spamdet.d1 import D1APIError, ReviewOutcome, SampleStats
from spamdet.review import ReviewService, render_pending_notification
from spamdet.schemas import MessageSnapshot, ModelSuggestion, SpamSample, UserSnapshot


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
            bio_state="present",
        ),
        matched_rule="blacklist_message:收米",
        model=model,
    )


class FakeTelegram:
    def __init__(self, *, chat=None, chat_error: Exception | None = None) -> None:
        self.sent = []
        self.chat = chat or SimpleNamespace(title="测试群组", username="test_group")
        self.chat_error = chat_error
        self.chat_ids = []

    async def get_chat(self, chat_id):
        self.chat_ids.append(chat_id)
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat

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
        self.group_pending = None

    async def next_group_pending(self):
        return self.group_pending

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
            sample=make_sample().to_dict(),
        )

    async def stats(self) -> SampleStats:
        return SampleStats(total=5, pending=2, reviewed=3, spam=2, ham=1)


class FakeMessage:
    def __init__(self, text: str = "待审核通知") -> None:
        self.text = text
        self.edits = []
        self.replies = []

    async def edit_text(
        self,
        text=None,
        parse_mode=None,
        entities=None,
        link_preview_options=None,
        reply_markup=None,
        rich_message=None,
    ):
        self.edits.append(
            (
                text,
                {
                    "parse_mode": parse_mode,
                    "entities": entities,
                    "link_preview_options": link_preview_options,
                    "reply_markup": reply_markup,
                    "rich_message": rich_message,
                },
            )
        )

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


class FakeOccurrenceCounter:
    def __init__(self, count: int = 1) -> None:
        self.value = count
        self.samples = []

    async def count(self, sample: SpamSample) -> int:
        self.samples.append(sample)
        return self.value


class ReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_and_review_result_show_occurrence_count(self) -> None:
        telegram, store = FakeTelegram(), FakeStore()
        counter = FakeOccurrenceCounter(8)
        service = ReviewService(
            telegram,
            store,
            admin_id=ADMIN_ID,
            occurrence_counter=counter,
        )

        await service.notify_pending(7, make_sample())
        callback = FakeCallback("sd:7:s")
        await service.handle_callback(callback)

        self.assertIn("<b>出现次数</b>：8", telegram.sent[0][0][1])
        self.assertIn("出现次数：8", callback.message.edits[0][0])

    async def test_admin_pulls_group_sample_for_manual_review(self) -> None:
        telegram = FakeTelegram()
        store = FakeStore()
        sample = make_sample()
        from dataclasses import replace
        sample = replace(sample, source="group", matched_rule=None)
        store.group_pending = SimpleNamespace(id=7, sample=sample.to_dict())
        service = ReviewService(telegram, store, admin_id=ADMIN_ID)
        self.assertTrue(await service.notify_next_group_pending())
        self.assertIn("普通群消息", telegram.sent[0][0][1])
        self.assertEqual(store.review_messages, [(7, ADMIN_ID, 88)])

    async def test_notification_is_escaped_and_has_exactly_two_buttons(self) -> None:
        telegram = FakeTelegram()
        store = FakeStore()
        service = ReviewService(telegram, store, admin_id=ADMIN_ID)

        await service.notify_pending(7, make_sample())

        self.assertEqual(len(telegram.sent), 1)
        args, kwargs = telegram.sent[0]
        self.assertEqual(args[0], ADMIN_ID)
        self.assertIn("&lt;b&gt;原始&amp;消息&lt;/b&gt;", args[1])
        self.assertIn('<b>群组</b>：<a href="https://t.me/test_group">测试群组</a>', args[1])
        self.assertEqual(telegram.chat_ids, [-100123])
        buttons = kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(buttons), 1)
        self.assertEqual([button.text for button in buttons[0]], ["✅ Spam", "❌ Not Spam"])
        self.assertEqual([button.callback_data for button in buttons[0]], ["sd:7:s", "sd:7:h"])
        self.assertTrue(kwargs["link_preview_options"].is_disabled)
        self.assertEqual(store.review_messages, [(7, ADMIN_ID, 88)])

    async def test_private_supergroup_name_links_to_original_group(self) -> None:
        telegram = FakeTelegram(chat=SimpleNamespace(title="私有 & 群", username=None))
        service = ReviewService(telegram, FakeStore(), admin_id=ADMIN_ID)

        await service.notify_pending(7, make_sample())

        rendered = telegram.sent[0][0][1]
        self.assertIn(
            '<b>群组</b>：<a href="https://t.me/c/123/42">私有 &amp; 群</a>',
            rendered,
        )

    async def test_group_lookup_failure_does_not_block_notification(self) -> None:
        telegram = FakeTelegram(chat_error=RuntimeError("failed"))
        service = ReviewService(telegram, FakeStore(), admin_id=ADMIN_ID)

        await service.notify_pending(7, make_sample())

        self.assertEqual(len(telegram.sent), 1)
        self.assertIn("<b>群组</b>：未知群组", telegram.sent[0][0][1])

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

    async def test_classifier_score_is_marked_un_calibrated(self) -> None:
        model = ModelSuggestion(
            version="char-logreg-v2-0123456789abcdef",
            kind="logistic_regression",
            decision="spam",
            score=0.75,
        )
        rendered = render_pending_notification(1, make_sample(model=model))
        self.assertIn("Spam 分数=0.7500（未校准）", rendered)
        self.assertNotIn("相似度=0.7500", rendered)

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
        self.assertTrue(callback.message.edits[0][1]["link_preview_options"].is_disabled)
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
