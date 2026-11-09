"""Spam 样本 v1 Schema 与 Pyrogram 消息快照提取。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any


SCHEMA_VERSION = 2
_SOURCES = frozenset({"rule", "manual", "group"})
_STATUSES = frozenset({"pending", "reviewed"})
_LABELS = frozenset({"spam", "ham"})
_BIO_STATES = frozenset({"present", "empty", "unavailable", "not_requested"})


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw_value = getattr(value, "value", value)
    return str(raw_value)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Kurigram 2.2.26 使用 datetime.fromtimestamp() 生成本地无时区时间。
        # timestamp() 会按同一进程的本地时区解释该值，再显式转为 UTC，
        # 避免把 Asia/Shanghai 的墙上时间直接错误标记成 Z。
        value = datetime.fromtimestamp(value.timestamp(), timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    type: str
    offset: int
    length: int
    url: str | None = None
    user_id: int | None = None
    language: str | None = None
    custom_emoji_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "offset": self.offset,
            "length": self.length,
            "url": self.url,
            "user_id": self.user_id,
            "language": self.language,
            "custom_emoji_id": self.custom_emoji_id,
        }


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    chat_id: int | None
    message_id: int | None
    date: str | None
    text: str
    caption: str | None
    entities: tuple[EntitySnapshot, ...] = ()
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("message.text 必须是字符串")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "date": self.date,
            "text": self.text,
            "caption": self.caption,
            "entities": [entity.to_dict() for entity in self.entities],
            "media_type": self.media_type,
        }


@dataclass(frozen=True, slots=True)
class UserSnapshot:
    id: int | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_premium: bool | None = None
    bio: str | None = None
    bio_state: str = "not_requested"

    def __post_init__(self) -> None:
        if self.bio_state not in _BIO_STATES:
            raise ValueError(f"不支持 bio_state={self.bio_state!r}")
        if self.bio_state == "present" and not self.bio:
            raise ValueError("bio_state=present 时 bio 必须是非空字符串")
        if self.bio_state != "present" and self.bio is not None:
            raise ValueError("只有 bio_state=present 时才能保存 bio")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_premium": self.is_premium,
            "bio": self.bio,
            "bio_state": self.bio_state,
        }


@dataclass(frozen=True, slots=True)
class ViaBotSnapshot:
    id: int | None = None
    username: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username}


@dataclass(frozen=True, slots=True)
class ModelSuggestion:
    version: str | None
    kind: str
    decision: str
    score: float | None
    nearest_example: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "kind": self.kind,
            "decision": self.decision,
            "score": self.score,
            "nearest_example": self.nearest_example,
        }


@dataclass(frozen=True, slots=True)
class SpamSample:
    source: str
    message: MessageSnapshot
    user: UserSnapshot = UserSnapshot()
    via_bot: ViaBotSnapshot = ViaBotSnapshot()
    matched_rule: str | None = None
    model: ModelSuggestion | None = None
    label: str | None = None
    status: str = "pending"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"不支持 schema_version={self.schema_version}")
        if self.source not in _SOURCES:
            raise ValueError(f"不支持 source={self.source!r}")
        if self.status not in _STATUSES:
            raise ValueError(f"不支持 status={self.status!r}")
        if self.label is not None and self.label not in _LABELS:
            raise ValueError(f"不支持 label={self.label!r}")
        if self.status == "pending" and self.label is not None:
            raise ValueError("pending 样本的 label 必须为 null")
        if self.status == "reviewed" and self.label is None:
            raise ValueError("reviewed 样本必须包含人工标签")
        if self.source == "manual" and (self.status != "reviewed" or self.label != "spam"):
            raise ValueError("manual 种子必须是人工确认的 reviewed/spam 样本")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "message": self.message.to_dict(),
            "user": self.user.to_dict(),
            "via_bot": self.via_bot.to_dict(),
            "matched_rule": self.matched_rule,
            "model": self.model.to_dict() if self.model is not None else None,
            "label": self.label,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _snapshot_entities(entities: Any, *, offset_base: int = 0) -> tuple[EntitySnapshot, ...]:
    result: list[EntitySnapshot] = []
    for entity in entities or ():
        user = getattr(entity, "user", None)
        result.append(
            EntitySnapshot(
                type=_enum_value(getattr(entity, "type", None)) or "unknown",
                offset=offset_base + int(getattr(entity, "offset", 0)),
                length=int(getattr(entity, "length", 0)),
                url=getattr(entity, "url", None),
                user_id=getattr(user, "id", None),
                language=getattr(entity, "language", None),
                custom_emoji_id=getattr(entity, "custom_emoji_id", None),
            )
        )
    return tuple(result)


def _utf16_length(value: str) -> int:
    """返回 Telegram entity offset 使用的 UTF-16 code unit 数。"""

    return len(value.encode("utf-16-le")) // 2


def _snapshot_text_components(components: Any) -> tuple[str, tuple[EntitySnapshot, ...]]:
    """合并多段结构化文字，并把各段 entity offset 映射到合并文本。"""

    parts: list[str] = []
    entities: list[EntitySnapshot] = []
    current_offset = 0
    for value, explicit_entities in components:
        if not isinstance(value, str):
            explicit_entities = getattr(value, "entities", explicit_entities)
            value = getattr(value, "text", None)
        if not isinstance(value, str) or not value:
            continue
        if parts:
            current_offset += 1  # 分隔各段的换行符占一个 UTF-16 code unit。
        parts.append(value)
        entities.extend(_snapshot_entities(explicit_entities, offset_base=current_offset))
        current_offset += _utf16_length(value)
    return "\n".join(parts), tuple(entities)


def _chat_display_components(chats: Any) -> list[tuple[str | None, None]]:
    result: list[tuple[str | None, None]] = []
    for chat in chats or ():
        result.append((getattr(chat, "title", None) or getattr(chat, "username", None), None))
    return result


def _snapshot_structured_content(message: Any) -> tuple[str, tuple[EntitySnapshot, ...]]:
    """提取 Kurigram 中不位于 Message.text/caption 的可见结构化文字。"""

    checklist = getattr(message, "checklist", None)
    if checklist is not None:
        components = [(getattr(checklist, "title", None), getattr(checklist, "entities", None))]
        components.extend(
            (getattr(task, "text", None), getattr(task, "entities", None))
            for task in (getattr(checklist, "tasks", None) or ())
        )
        return _snapshot_text_components(components)

    poll = getattr(message, "poll", None)
    if poll is not None:
        components = [(getattr(poll, "question", None), None)]
        components.extend((getattr(option, "text", None), None) for option in (getattr(poll, "options", None) or ()))
        components.extend(
            ((getattr(poll, "explanation", None), None), (getattr(poll, "description", None), None))
        )
        return _snapshot_text_components(components)

    contact = getattr(message, "contact", None)
    if contact is not None:
        return _snapshot_text_components(
            (
                (getattr(contact, "first_name", None), None),
                (getattr(contact, "last_name", None), None),
                (getattr(contact, "phone_number", None), None),
            )
        )

    venue = getattr(message, "venue", None)
    if venue is not None:
        return _snapshot_text_components(
            ((getattr(venue, "title", None), None), (getattr(venue, "address", None), None))
        )

    location = getattr(message, "location", None)
    if location is not None:
        return _snapshot_text_components(((getattr(location, "address", None), None),))

    game = getattr(message, "game", None)
    if game is not None:
        return _snapshot_text_components(
            (
                (getattr(game, "title", None), None),
                (getattr(game, "description", None), None),
                (getattr(game, "short_name", None), None),
            )
        )

    invoice = getattr(message, "invoice", None)
    if invoice is not None:
        components = [
            (getattr(invoice, "title", None), None),
            (getattr(invoice, "description", None), None),
            (getattr(invoice, "start_parameter", None), None),
        ]
        components.extend((getattr(price, "label", None), None) for price in (getattr(invoice, "prices", None) or ()))
        return _snapshot_text_components(components)

    giveaway = getattr(message, "giveaway", None)
    if giveaway is not None:
        components = [(getattr(giveaway, "description", None), None)]
        components.extend(_chat_display_components(getattr(giveaway, "chats", None)))
        return _snapshot_text_components(components)

    giveaway_winners = getattr(message, "giveaway_winners", None)
    if giveaway_winners is not None:
        components = [(getattr(giveaway_winners, "prize_description", None), None)]
        components.extend(_chat_display_components((getattr(giveaway_winners, "chat", None),)))
        return _snapshot_text_components(components)

    story = getattr(message, "story", None)
    if story is not None:
        return _snapshot_text_components(((getattr(story, "caption", None), getattr(story, "caption_entities", None)),))

    web_page = getattr(message, "web_page", None)
    if web_page is not None:
        return _snapshot_text_components(
            (
                (getattr(web_page, "url", None), None),
                (getattr(web_page, "site_name", None), None),
                (getattr(web_page, "title", None), None),
                (getattr(web_page, "description", None), None),
                (getattr(web_page, "author", None), None),
            )
        )

    audio = getattr(message, "audio", None)
    if audio is not None:
        return _snapshot_text_components(
            (
                (getattr(audio, "performer", None), None),
                (getattr(audio, "title", None), None),
                (getattr(audio, "file_name", None), None),
            )
        )

    for attribute in ("document", "video", "animation"):
        media = getattr(message, attribute, None)
        if media is not None:
            return _snapshot_text_components(((getattr(media, "file_name", None), None),))

    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        return _snapshot_text_components(
            (
                (getattr(sticker, "emoji", None), None),
                (getattr(sticker, "set_name", None), None),
                (getattr(sticker, "file_name", None), None),
            )
        )
    return "", ()


def sample_from_message(
    message: Any,
    *,
    matched_rule: str | None = None,
    bio: str | None = None,
    bio_state: str | None = None,
    source: str = "rule",
) -> SpamSample:
    """同步提取 Pyrogram ``Message`` 的必要字段，不执行任何 API 请求。"""

    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    from_user = getattr(message, "from_user", None)
    via_bot = getattr(message, "via_bot", None)
    chat = getattr(message, "chat", None)
    entity_source = getattr(message, "entities", None) if text is not None else getattr(message, "caption_entities", None)

    resolved_bio_state = bio_state or ("present" if bio else "not_requested")
    return SpamSample(
        source=source,
        message=MessageSnapshot(
            chat_id=getattr(chat, "id", None),
            message_id=getattr(message, "id", None),
            date=_utc_iso(getattr(message, "date", None)),
            text=text if text is not None else "",
            caption=caption,
            entities=_snapshot_entities(entity_source),
            media_type=_enum_value(getattr(message, "media", None)),
        ),
        user=UserSnapshot(
            id=getattr(from_user, "id", None),
            username=getattr(from_user, "username", None),
            first_name=getattr(from_user, "first_name", None),
            last_name=getattr(from_user, "last_name", None),
            is_premium=getattr(from_user, "is_premium", None),
            bio=bio,
            bio_state=resolved_bio_state,
        ),
        via_bot=ViaBotSnapshot(
            id=getattr(via_bot, "id", None),
            username=getattr(via_bot, "username", None),
        ),
        matched_rule=matched_rule,
    )


def sample_from_admin_forward(message: Any) -> SpamSample:
    """将管理员转发的消息转换为人工确认 Spam，不把转发者当成原作者。"""

    origin = getattr(message, "forward_origin", None)
    if origin is None:
        raise ValueError("消息不是 Telegram 转发消息")

    origin_type = _enum_value(getattr(origin, "type", None)) or "unknown"
    origin_user = getattr(origin, "sender_user", None)
    origin_chat = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)

    if origin_user is not None:
        user = UserSnapshot(
            id=getattr(origin_user, "id", None),
            username=getattr(origin_user, "username", None),
            first_name=getattr(origin_user, "first_name", None),
            last_name=getattr(origin_user, "last_name", None),
            is_premium=getattr(origin_user, "is_premium", None),
        )
    elif origin_chat is not None:
        user = UserSnapshot(
            id=getattr(origin_chat, "id", None),
            username=getattr(origin_chat, "username", None),
            first_name=getattr(origin_chat, "title", None),
            last_name=getattr(origin, "author_signature", None),
        )
    else:
        user = UserSnapshot(first_name=getattr(origin, "sender_user_name", None))

    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    structured_text, structured_entities = _snapshot_structured_content(message)
    if text is None and caption is None and structured_text:
        text = structured_text
        entity_snapshots = structured_entities
    else:
        entity_source = (
            getattr(message, "entities", None)
            if text is not None
            else getattr(message, "caption_entities", None)
        )
        entity_snapshots = _snapshot_entities(entity_source)
    if not ((isinstance(text, str) and text.strip()) or (isinstance(caption, str) and caption.strip())):
        raise ValueError("转发消息必须包含可提取的文字内容")

    via_bot = getattr(message, "via_bot", None)
    return SpamSample(
        source="manual",
        message=MessageSnapshot(
            chat_id=getattr(origin_chat, "id", None),
            message_id=getattr(origin, "message_id", None),
            date=_utc_iso(getattr(origin, "date", None)),
            text=text if text is not None else "",
            caption=caption,
            entities=entity_snapshots,
            media_type=_enum_value(getattr(message, "media", None)),
        ),
        user=user,
        via_bot=ViaBotSnapshot(
            id=getattr(via_bot, "id", None),
            username=getattr(via_bot, "username", None),
        ),
        matched_rule=f"admin_forward:{origin_type}",
        label="spam",
        status="reviewed",
    )


def sample_from_stored_dict(value: dict[str, Any]) -> SpamSample:
    """只重建审核展示所需的已存快照，不伪造 Pyrogram Message。"""
    message = value["message"]
    user = value["user"]
    model = value.get("model")
    return SpamSample(
        source=value["source"],
        message=MessageSnapshot(
            chat_id=message.get("chat_id"), message_id=message.get("message_id"),
            date=message.get("date"), text=message.get("text", ""),
            caption=message.get("caption"), media_type=message.get("media_type"),
        ),
        user=UserSnapshot(
            id=user.get("id"), username=user.get("username"),
            first_name=user.get("first_name"), last_name=user.get("last_name"),
            is_premium=user.get("is_premium"), bio=user.get("bio"),
            bio_state=user.get("bio_state", "not_requested"),
        ),
        matched_rule=value.get("matched_rule"),
        model=ModelSuggestion(**model) if isinstance(model, dict) else None,
    )


def sample_from_seed_text(text: str) -> SpamSample:
    """将用户确认的纯文本正例表示为样本，不伪造 Pyrogram 对象。"""

    if not isinstance(text, str):
        raise TypeError("种子文本必须是字符串")
    return SpamSample(
        source="manual",
        message=MessageSnapshot(
            chat_id=None,
            message_id=None,
            date=None,
            text=text,
            caption=None,
        ),
        label="spam",
        status="reviewed",
    )
