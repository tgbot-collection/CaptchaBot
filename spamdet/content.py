"""Kurigram/Pyrogram Message 的同步可见文字提取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    type: str
    offset: int
    length: int
    url: str | None = None
    user_id: int | None = None
    language: str | None = None
    custom_emoji_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedMessageContent:
    """可同时用于规则匹配和统一样本持久化的消息文字。"""

    text: str
    caption: str | None
    entities: tuple[ExtractedEntity, ...]
    source: str

    @property
    def search_text(self) -> str:
        """规则匹配使用的完整可见文字；caption 不会被伪装成持久化 text。"""

        return self.text or self.caption or ""


def _extract_entities(entities: Any, *, offset_base: int = 0) -> tuple[ExtractedEntity, ...]:
    result: list[ExtractedEntity] = []
    for entity in entities or ():
        user = getattr(entity, "user", None)
        result.append(
            ExtractedEntity(
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
    return len(value.encode("utf-16-le")) // 2


def _join_components(components: Iterable[tuple[Any, Any]]) -> tuple[str, tuple[ExtractedEntity, ...]]:
    parts: list[str] = []
    entities: list[ExtractedEntity] = []
    current_offset = 0
    for value, explicit_entities in components:
        if not isinstance(value, str):
            explicit_entities = getattr(value, "entities", explicit_entities)
            value = getattr(value, "text", None)
        if not isinstance(value, str) or not value:
            continue
        if parts:
            current_offset += 1
        parts.append(value)
        entities.extend(_extract_entities(explicit_entities, offset_base=current_offset))
        current_offset += _utf16_length(value)
    return "\n".join(parts), tuple(entities)


def _chat_components(chats: Any) -> list[tuple[str | None, None]]:
    return [
        (getattr(chat, "title", None) or getattr(chat, "username", None), None)
        for chat in (chats or ())
        if chat is not None
    ]


def _extract_structured_content(message: Any) -> tuple[str, tuple[ExtractedEntity, ...], str]:
    checklist = getattr(message, "checklist", None)
    if checklist is not None:
        components = [(getattr(checklist, "title", None), getattr(checklist, "entities", None))]
        components.extend(
            (getattr(task, "text", None), getattr(task, "entities", None))
            for task in (getattr(checklist, "tasks", None) or ())
        )
        text, entities = _join_components(components)
        return text, entities, "checklist"

    poll = getattr(message, "poll", None)
    if poll is not None:
        components = [(getattr(poll, "question", None), None)]
        components.extend((getattr(option, "text", None), None) for option in (getattr(poll, "options", None) or ()))
        components.extend(
            ((getattr(poll, "explanation", None), None), (getattr(poll, "description", None), None))
        )
        text, entities = _join_components(components)
        return text, entities, "poll"

    contact = getattr(message, "contact", None)
    if contact is not None:
        text, entities = _join_components(
            (
                (getattr(contact, "first_name", None), None),
                (getattr(contact, "last_name", None), None),
                (getattr(contact, "phone_number", None), None),
            )
        )
        return text, entities, "contact"

    venue = getattr(message, "venue", None)
    if venue is not None:
        text, entities = _join_components(
            ((getattr(venue, "title", None), None), (getattr(venue, "address", None), None))
        )
        return text, entities, "venue"

    location = getattr(message, "location", None)
    if location is not None:
        text, entities = _join_components(((getattr(location, "address", None), None),))
        return text, entities, "location"

    game = getattr(message, "game", None)
    if game is not None:
        text, entities = _join_components(
            (
                (getattr(game, "title", None), None),
                (getattr(game, "description", None), None),
                (getattr(game, "short_name", None), None),
            )
        )
        return text, entities, "game"

    invoice = getattr(message, "invoice", None)
    if invoice is not None:
        components = [
            (getattr(invoice, "title", None), None),
            (getattr(invoice, "description", None), None),
            (getattr(invoice, "start_parameter", None), None),
        ]
        components.extend((getattr(price, "label", None), None) for price in (getattr(invoice, "prices", None) or ()))
        text, entities = _join_components(components)
        return text, entities, "invoice"

    giveaway = getattr(message, "giveaway", None)
    if giveaway is not None:
        components = [(getattr(giveaway, "description", None), None)]
        components.extend(_chat_components(getattr(giveaway, "chats", None)))
        text, entities = _join_components(components)
        return text, entities, "giveaway"

    giveaway_winners = getattr(message, "giveaway_winners", None)
    if giveaway_winners is not None:
        components = [(getattr(giveaway_winners, "prize_description", None), None)]
        components.extend(_chat_components((getattr(giveaway_winners, "chat", None),)))
        text, entities = _join_components(components)
        return text, entities, "giveaway_winners"

    story = getattr(message, "story", None)
    if story is not None:
        text, entities = _join_components(
            ((getattr(story, "caption", None), getattr(story, "caption_entities", None)),)
        )
        return text, entities, "story"

    web_page = getattr(message, "web_page", None)
    if web_page is not None:
        text, entities = _join_components(
            (
                (getattr(web_page, "url", None), None),
                (getattr(web_page, "site_name", None), None),
                (getattr(web_page, "title", None), None),
                (getattr(web_page, "description", None), None),
                (getattr(web_page, "author", None), None),
            )
        )
        return text, entities, "web_page"

    audio = getattr(message, "audio", None)
    if audio is not None:
        text, entities = _join_components(
            (
                (getattr(audio, "performer", None), None),
                (getattr(audio, "title", None), None),
                (getattr(audio, "file_name", None), None),
            )
        )
        return text, entities, "audio"

    for attribute in ("document", "video", "animation"):
        media = getattr(message, attribute, None)
        if media is not None:
            text, entities = _join_components(((getattr(media, "file_name", None), None),))
            return text, entities, attribute

    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        text, entities = _join_components(
            (
                (getattr(sticker, "emoji", None), None),
                (getattr(sticker, "set_name", None), None),
                (getattr(sticker, "file_name", None), None),
            )
        )
        return text, entities, "sticker"
    return "", (), "empty"


def extract_message_content(message: Any) -> ExtractedMessageContent:
    """同步提取消息可见文字；不调用 Telegram API，不执行 OCR/ASR。"""

    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return ExtractedMessageContent(
            text=text,
            caption=getattr(message, "caption", None),
            entities=_extract_entities(getattr(message, "entities", None)),
            source="text",
        )

    caption = getattr(message, "caption", None)
    if isinstance(caption, str) and caption:
        return ExtractedMessageContent(
            text="",
            caption=caption,
            entities=_extract_entities(getattr(message, "caption_entities", None)),
            source="caption",
        )

    structured_text, entities, source = _extract_structured_content(message)
    return ExtractedMessageContent(
        text=structured_text,
        caption=None,
        entities=entities,
        source=source,
    )
