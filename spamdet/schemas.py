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


def _snapshot_entities(entities: Any) -> tuple[EntitySnapshot, ...]:
    result: list[EntitySnapshot] = []
    for entity in entities or ():
        user = getattr(entity, "user", None)
        result.append(
            EntitySnapshot(
                type=_enum_value(getattr(entity, "type", None)) or "unknown",
                offset=int(getattr(entity, "offset", 0)),
                length=int(getattr(entity, "length", 0)),
                url=getattr(entity, "url", None),
                user_id=getattr(user, "id", None),
                language=getattr(entity, "language", None),
                custom_emoji_id=getattr(entity, "custom_emoji_id", None),
            )
        )
    return tuple(result)


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
