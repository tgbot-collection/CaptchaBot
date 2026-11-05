"""Telegram 单管理员审核通知与回调处理。"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Protocol

from pyrogram import enums, types

from .d1 import D1Error, ReviewOutcome, SampleStats, StoredSample
from .schemas import ModelSuggestion, SpamSample


_CALLBACK_RE = re.compile(r"^sd:([1-9]\d*):([sh])$")
_MESSAGE_LIMIT = 1500
_BIO_LIMIT = 500
_FIELD_LIMIT = 300


class ReviewStore(Protocol):
    async def set_review_message(self, sample_id: int, chat_id: int, message_id: int) -> None: ...

    async def review(self, sample_id: int, label: str, admin_id: int) -> ReviewOutcome: ...

    async def get(self, sample_id: int) -> StoredSample: ...

    async def stats(self) -> SampleStats: ...

    async def next_group_pending(self) -> StoredSample | None: ...


def _safe(value: str | None, limit: int, *, empty: str = "无") -> str:
    """按 HTML 转义后的长度安全截断，不切断转义实体。"""

    source = value or empty
    pieces: list[str] = []
    length = 0
    for character in source:
        escaped = html.escape(character, quote=True)
        if length + len(escaped) > limit - 1:
            return "".join(pieces) + "…"
        pieces.append(escaped)
        length += len(escaped)
    return "".join(pieces)


def _model_text(model: ModelSuggestion | None) -> str:
    if model is None:
        return "暂无（不影响人工审核）"
    decision = _safe(model.decision, 120)
    if model.decision == "spam_like":
        decision += "（与已确认 Spam 种子较相似，仅供参考）"
    elif model.decision == "unknown":
        decision += "（无法判断，不等于 Ham）"
    elif model.decision == "unavailable":
        decision += "（模型不可用，不影响保存和人工审核）"
    score = "无" if model.score is None else f"{model.score:.4f}"
    score_text = (
        f"Spam 分数={score}（未校准）"
        if model.kind == "logistic_regression"
        else f"相似度={score}（不是概率）"
    )
    return (
        f"{decision} / {score_text} / "
        f"{_safe(model.kind, 120)} / 版本={_safe(model.version, 120)}"
    )


def render_pending_notification(sample_id: int, sample: SpamSample) -> str:
    """生成只用于管理员私聊的 HTML 文本。"""

    content = sample.message.text or sample.message.caption
    full_name = " ".join(part for part in (sample.user.first_name, sample.user.last_name) if part)
    username = f"@{sample.user.username}" if sample.user.username else None
    return "\n".join(
        (
            f"<b>Spam 数据审核 #{sample_id}</b>",
            "",
            f"<b>消息</b>\n{_safe(content, _MESSAGE_LIMIT, empty='[无文本内容]')}",
            f"<b>昵称</b>：{_safe(full_name, _FIELD_LIMIT)}",
            f"<b>用户名</b>：{_safe(username, _FIELD_LIMIT)}",
            f"<b>Bio</b>：{_safe(sample.user.bio, _BIO_LIMIT)}",
            f"<b>来源</b>：{'普通群消息' if sample.source == 'group' else '规则命中'}",
            f"<b>命中规则</b>：{_safe(sample.matched_rule, _FIELD_LIMIT)}",
            f"<b>模型建议</b>：{_model_text(sample.model)}",
        )
    )


def render_dataset_stats(stats: SampleStats) -> str:
    return (
        "Spam 数据集统计\n"
        f"total: {stats.total}\n"
        f"pending: {stats.pending}\n"
        f"reviewed: {stats.reviewed}\n"
        f"spam: {stats.spam}\n"
        f"ham: {stats.ham}"
    )


class ReviewService:
    """发送通知并将单管理员的审核原子写入 D1。"""

    def __init__(self, telegram_client: Any, store: ReviewStore, *, admin_id: int) -> None:
        if admin_id <= 0:
            raise ValueError("admin_id 必须是正整数")
        self._telegram = telegram_client
        self._store = store
        self._admin_id = admin_id

    @property
    def admin_id(self) -> int:
        return self._admin_id

    async def notify_pending(self, sample_id: int, sample: SpamSample) -> None:
        stored = await self._store.get(sample_id)
        if stored.status != "pending" or stored.review_message_id is not None:
            return
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton("✅ Spam", callback_data=f"sd:{sample_id}:s"),
                    types.InlineKeyboardButton("❌ Not Spam", callback_data=f"sd:{sample_id}:h"),
                ]
            ]
        )
        sent = await self._telegram.send_message(
            self._admin_id,
            render_pending_notification(sample_id, sample),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )
        await self._store.set_review_message(sample_id, self._admin_id, int(sent.id))

    async def notify_next_group_pending(self) -> bool:
        stored = await self._store.next_group_pending()
        if stored is None:
            return False
        from .schemas import sample_from_stored_dict
        await self.notify_pending(stored.id, sample_from_stored_dict(stored.sample))
        return True

    async def handle_callback(self, callback_query: Any) -> None:
        match = _CALLBACK_RE.fullmatch(str(getattr(callback_query, "data", "")))
        if match is None:
            await callback_query.answer("无效的审核操作", show_alert=True)
            return

        actor_id = getattr(getattr(callback_query, "from_user", None), "id", None)
        if actor_id != self._admin_id:
            await callback_query.answer("你没有审核权限", show_alert=True)
            return

        sample_id = int(match.group(1))
        requested_label = "spam" if match.group(2) == "s" else "ham"
        # D1 最长可能包含重试，先应答以避免 Telegram callback 超时。
        await callback_query.answer("正在保存审核结果…")

        try:
            outcome = await self._store.review(sample_id, requested_label, self._admin_id)
            if outcome == ReviewOutcome.ALREADY_REVIEWED:
                stored = await self._store.get(sample_id)
                final_label = stored.label
            else:
                final_label = requested_label
        except D1Error as exc:
            logging.warning("Spam 审核写入失败（%s）", type(exc).__name__)
            await self._report_failure(callback_query)
            return

        result_text = "Spam" if final_label == "spam" else "Not Spam"
        suffix = f"\n\n审核结果：{result_text}"
        if outcome == ReviewOutcome.ALREADY_REVIEWED:
            suffix += "（此样本已审核，未覆盖原标签）"
        try:
            original = str(getattr(callback_query.message, "text", "") or "Spam 数据审核")
            await callback_query.message.edit_text(
                original + suffix,
                parse_mode=enums.ParseMode.DISABLED,
                disable_web_page_preview=True,
                reply_markup=None,
            )
        except Exception as exc:
            # D1 已完成人工标注，Telegram 编辑失败不能回滚或重复改标签。
            logging.warning("Spam 审核通知更新失败（%s）", type(exc).__name__)

    async def dataset_stats_text(self) -> str:
        return render_dataset_stats(await self._store.stats())

    async def _report_failure(self, callback_query: Any) -> None:
        try:
            await callback_query.message.reply_text(
                "审核结果未保存，原按钮保留，请稍后重试。",
                parse_mode=enums.ParseMode.DISABLED,
            )
        except Exception as exc:
            logging.warning("Spam 审核失败提示发送失败（%s）", type(exc).__name__)
