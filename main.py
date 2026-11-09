#!/usr/bin/env python3
# coding: utf-8


__author__ = "Benny <benny.think@gmail.com>"

import asyncio
import contextlib
import logging
import os
import random
import re
import string
import time

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from captcha.image import ImageCaptcha
from pyrogram import Client, enums, filters, types
from pyrogram.raw import functions as raw_functions
from pyrogram.raw import types as raw_types
from zhconv import convert

from spamdet import (
    CaptureService,
    D1Client,
    D1Error,
    ExtractedMessageContent,
    InferenceService,
    ReviewService,
    SpamLearningConfig,
    extract_message_content,
    sample_from_admin_forward,
    sample_from_message,
    render_model_status,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("apscheduler.executors.default").propagate = False


try:
    from dotenv import load_dotenv
    load_dotenv(verbose=True)
except ImportError:
    logging.warning("Dotenv not found")

APP_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS = os.getenv("REDIS", "localhost")

workers = min(128, max(8, (os.cpu_count() or 1) * 8))
app = Client("captchabot", APP_ID, API_HASH, bot_token=BOT_TOKEN, workers=workers)
redis_client = aioredis.StrictRedis(host=REDIS, decode_responses=True, db=0)
image = ImageCaptcha()
PREDEFINED_STR = re.sub(r"[1l0oOI]", "", string.ascii_letters + string.digits)
IDLE_SECONDS = 120
scheduler = AsyncIOScheduler()
spamdet_config = SpamLearningConfig.from_env()
spam_capture_service: CaptureService | None = None
spam_d1_client: D1Client | None = None
spam_review_service: ReviewService | None = None
spam_inference_service: InferenceService | None = None


def generate_char():
    return "".join([random.choice(PREDEFINED_STR) for _ in range(5)])


@app.on_message(filters.command(["start", "help"]) &  filters.private)
async def start_handler(client: "Client", message: "types.Message"):
    logging.info("Welcome to Captcha Bot")
    await message.reply_text("Hello! Add me to a group and make me admin!")


@app.on_message(filters.command("dataset") & filters.private)
async def dataset_handler(client: "Client", message: "types.Message"):
    service = spam_review_service
    actor_id = getattr(getattr(message, "from_user", None), "id", None)
    if service is None or actor_id != service.admin_id:
        await message.reply_text("你没有查看数据集统计的权限。")
        return
    try:
        text = await service.dataset_stats_text()
    except D1Error as exc:
        logging.warning("Spam 数据集统计失败（%s）", type(exc).__name__)
        await message.reply_text("数据集统计暂时不可用，请稍后重试。")
        return
    await message.reply_text(text)


@app.on_message(filters.command("model") & filters.private)
async def model_handler(client: "Client", message: "types.Message"):
    service = spam_inference_service
    actor_id = getattr(getattr(message, "from_user", None), "id", None)
    if service is None or actor_id != spamdet_config.admin_id:
        await message.reply_text("你没有查看模型状态的权限。")
        return
    await message.reply_text(render_model_status(service.status))


@app.on_message(filters.command("review") & filters.private)
async def review_next_handler(client: "Client", message: "types.Message"):
    service = spam_review_service
    if service is None or getattr(getattr(message, "from_user", None), "id", None) != service.admin_id:
        await message.reply_text("你没有审核权限。")
        return
    try:
        if not await service.notify_next_group_pending():
            await message.reply_text("暂无待领取的普通群消息。")
    except D1Error as exc:
        logging.warning("普通群消息审核读取失败（%s）", type(exc).__name__)
        await message.reply_text("审核数据暂时不可用，请稍后重试。")


@app.on_message(filters.private & filters.incoming & filters.forwarded)
async def admin_forwarded_spam_handler(client: "Client", message: "types.Message"):
    """管理员私聊转发即代表人工确认 Spam；不使用管理员资料构造样本。"""

    actor_id = getattr(getattr(message, "from_user", None), "id", None)
    if actor_id != spamdet_config.admin_id:
        await message.reply_text("你没有提交 Spam 样本的权限。")
        return
    if spam_d1_client is None:
        await message.reply_text("Spam 学习功能当前未启用，消息未保存。")
        return
    try:
        sample = sample_from_admin_forward(message)
        if sample.user.id == actor_id:
            await message.reply_text("不能将管理员本人作为 Spam 原作者，消息未保存。")
            return
        result = await spam_d1_client.import_forwarded_spam(sample, actor_id)
    except ValueError as exc:
        logging.info("管理员转发样本未保存（%s）", type(exc).__name__)
        await message.reply_text("转发消息没有可保存的文字内容。")
        return
    except D1Error as exc:
        logging.warning("管理员转发 Spam 写入失败（%s）", type(exc).__name__)
        await message.reply_text("D1 写入失败，消息未保存，请稍后重试。")
        return

    action = "已保存" if result.created else "已存在，未重复保存"
    await message.reply_text(f"Spam 样本{action}，ID={result.id}。")


@app.on_message(filters.new_chat_members)  # only service message
async def new_chat(client: "Client", message: "types.Message"):
    logging.info("new chat member: %s", message.from_user)
    if await group_message_preprocess(client, message):
        return

    from_user_id = message.from_user.id
    await restrict_user(message.chat.id, from_user_id)
    chars = generate_char()
    data = image.generate(chars)
    data.name = f"{message.id}-captcha.png"

    user_button = []
    for _ in range(6):
        fake_char = generate_char()
        user_button.append(types.InlineKeyboardButton(text=fake_char, callback_data=f"{fake_char},{from_user_id}"))

    user_button[random.randint(0, len(user_button) - 1)] = types.InlineKeyboardButton(
        text=chars,
        callback_data=f"{chars},{from_user_id}",
    )

    user_button = [user_button[i : i + 3] for i in range(0, len(user_button), 3)]
    markup = types.InlineKeyboardMarkup(
        [
            user_button[0],
            user_button[1],
            [
                types.InlineKeyboardButton("Approve", callback_data=f"Approve,{from_user_id}"),
                types.InlineKeyboardButton("Deny", callback_data=f"Deny,{from_user_id}"),
            ],
        ]
    )
    bot_message = await client.send_photo(
        chat_id=message.chat.id,
        photo=data,
        caption=f"Hello [{message.from_user.first_name}](tg://user?id={from_user_id}), "
        f"please verify by clicking correct buttons in {IDLE_SECONDS} seconds",
        reply_markup=markup,
    )

    group_id = message.chat.id
    message_id = bot_message.id
    # redis data structure: name: group_id,chat_id  k-v: created:timestamp, message_id:id, captcha:chars
    name = f"{group_id},{from_user_id}"
    mapping = {"created": str(time.time()), "message_id": str(message_id), "captcha": chars}
    await redis_client.hset(name, mapping=mapping)
    #  deleting service message and ignoring error
    with contextlib.suppress(Exception):
        await message.delete()


@app.on_callback_query(filters.regex(r"Approve.*"))
async def admin_approve(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id
    join_user_id = callback_query.data.split(",")[1]
    # Get administrators
    administrators = []
    async for m in app.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
        administrators.append(m.user.id)
    if from_user_id in administrators:
        await callback_query.answer("Approved")
        await callback_query.message.delete()
        await un_restrict_user(chat_id, join_user_id)
        await invalid_queue(f"{chat_id},{join_user_id}")
    else:
        await callback_query.answer("You are not administrator")


@app.on_callback_query(filters.regex(r"Deny.*"))
async def admin_deny(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id  # this is admin
    join_user_id = callback_query.data.split(",")[1]

    administrators = []
    async for m in app.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
        administrators.append(m.user.id)
    if from_user_id in administrators:
        await callback_query.answer("Denied")
        await callback_query.message.delete()
        await ban_user(chat_id, join_user_id)
        await invalid_queue(f"{chat_id},{join_user_id}")
    else:
        await callback_query.answer("You are not administrator")


@app.on_callback_query(filters.regex(r"^sd:[1-9]\d*:[sh]$"))
async def spam_review_callback(client: "Client", callback_query: types.CallbackQuery):
    service = spam_review_service
    if service is None:
        await callback_query.answer("审核功能当前不可用", show_alert=True)
        return
    await service.handle_callback(callback_query)


# 验证码历史回调保持 catch-all，但必须显式排除 Spam 审核协议。
@app.on_callback_query(~filters.regex(r"^sd:[1-9]\d*:[sh]$"))
async def user_press(client: "Client", callback_query: types.CallbackQuery):
    click_user = callback_query.from_user.id
    join_user_id = callback_query.data.split(",")[1]
    if str(click_user) != join_user_id:
        await callback_query.answer("Not your button.")
        return

    group_id = callback_query.message.chat.id
    msg_id = callback_query.message.id
    correct_result = await redis_client.hget(f"{group_id},{join_user_id}", "captcha")
    user_result = callback_query.data.split(",")[0]
    logging.info("User %s click %s, correct answer:%s", click_user, user_result, correct_result)

    if user_result == correct_result:
        await callback_query.answer("Welcome!")
        await un_restrict_user(group_id, join_user_id)
    else:
        await callback_query.answer("Wrong answer")
        await ban_user(group_id, join_user_id)

    logging.info("Deleting inline button...")
    await callback_query.message.delete()
    await invalid_queue(f"{group_id},{join_user_id}")


async def restrict_user(gid, uid):
    logging.info("restrict user %s in group %s", uid, gid)
    # this method may throw an error if bot is not admin, so we just ignore it
    with contextlib.suppress(Exception):
        await app.restrict_chat_member(gid, uid, types.ChatPermissions())


async def ban_user(gid, uid):
    logging.info("ban user %s in group %s", uid, gid)
    with contextlib.suppress(Exception):
        await app.ban_chat_member(gid, uid)

    # only for dev
    if os.getenv("MODE") == "dev":
        await asyncio.sleep(5)
        logging.warning("DEBUG MODE: Remove user from banning list")
        await app.unban_chat_member(gid, uid)


async def un_restrict_user(gid, uid):
    logging.info("unban user %s in group %s", uid, gid)
    await app.restrict_chat_member(
        int(gid),
        int(uid),
        types.ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_send_polls=True,
            can_add_web_page_previews=True,
            can_invite_users=False,
            can_change_info=False,
            can_pin_messages=False,
        ),
    )


async def invalid_queue(gid_uid):
    # for debugging purpose, just set deleted to true
    await redis_client.delete(gid_uid)


async def check_idle_verification():
    items = await redis_client.keys("*")
    value = None
    for gid_uid in items:
        await asyncio.sleep(1)
        try:
            value = await redis_client.hgetall(gid_uid)
            group_id, from_user_id = [int(i) for i in gid_uid.split(",")]
            created_at = float(value.get("created", 0))
            message_id = int(value.get("message_id", 0))
            if time.time() - created_at > IDLE_SECONDS:
                logging.info("User %s group %s timeout, message id %s", from_user_id, group_id, message_id)
                # ban user, delete captcha and remove from redis
                await ban_user(group_id, from_user_id)
                await delete_captcha(group_id, from_user_id, message_id)
            else:
                logging.info("User %s in group %s still in verification queue", from_user_id, group_id)
        except Exception as e:
            logging.info("redis data %s is not correct:%s", value, e)


async def delete_captcha(group_id, from_user_id, message_id):
    logging.info("preparing to delete captcha message %s %s in group %s", from_user_id, message_id, group_id)
    try:
        msg = await app.get_messages(group_id, message_id)
        await msg.delete()
        await invalid_queue(f"{group_id},{from_user_id}")
    except Exception as e:
        logging.error("Failed to delete message %s %s in group %s: %s", from_user_id, message_id, group_id, e)


def keyword_hit(keyword: str, message: str | None) -> bool:
    if message is None:
        message = ""
    return keyword.lower() in convert(message.lower(), "zh-cn")


async def delete_rule_message(
    message: "types.Message",
    *,
    matched_rule: str,
    bio: str | None = None,
    bio_state: str | None = None,
    content: ExtractedMessageContent | None = None,
) -> None:
    """先同步快照，再执行原删除；删除尝试后非阻塞提交采集。"""

    service = spam_capture_service
    sample = None
    if service is not None:
        try:
            sample = sample_from_message(
                message,
                matched_rule=matched_rule,
                bio=bio,
                bio_state=bio_state,
                content=content,
            )
        except Exception as exc:
            # 快照异常不能阻止既有删除，也不记录可能含敏感数据的异常原文。
            logging.warning("Spam 样本快照失败（%s）", type(exc).__name__)

    try:
        await message.delete()
    finally:
        if service is not None and sample is not None:
            try:
                service.submit(sample)
            except Exception as exc:
                logging.warning("Spam 样本入队失败（%s）", type(exc).__name__)


async def delete_guest_bot_trigger(client: "Client", message: "types.Message"):
    caller_id = getattr(getattr(message, "guest_bot_caller_user", None), "id", None)
    if caller_id is None:
        return

    trigger = getattr(message, "reply_to_message", None)
    reply_id = getattr(message, "reply_to_message_id", None)
    trigger_id = getattr(trigger, "id", None) or reply_id or message.id - 1
    if trigger_id <= 0:
        return

    try:
        if trigger is None:
            trigger = await client.get_messages(message.chat.id, trigger_id)
        if getattr(getattr(trigger, "from_user", None), "id", None) != caller_id:
            return
        if reply_id is None and getattr(message, "reply_to_message", None) is None:
            # An adjacent message is only a guess: also verify the bot mention.
            bot_username = getattr(message.from_user, "username", None)
            text = trigger.text or trigger.caption or ""
            if not bot_username or not re.search(r"@" + re.escape(bot_username) + r"\b", text, re.IGNORECASE):
                return
        await delete_rule_message(trigger, matched_rule="message_signal:guest_bot_trigger")
    except Exception as e:
        logging.warning("Failed to delete guest bot trigger (%s)", type(e).__name__)


# only group incoming message, ignore service message
@app.on_message(filters.group & filters.incoming & ~filters.service)
@app.on_edited_message(filters.group & filters.incoming & ~filters.service)
async def group_message_preprocess(client: "Client", message: "types.Message"):
    # Check message-local spam signals before any user or sticker API requests.
    content = extract_message_content(message)
    user_message = content.search_text
    sender_username = (getattr(message.from_user, "username", None) or "").lower()
    has_inline_buttons = isinstance(message.reply_markup, types.InlineKeyboardMarkup) and any(
        message.reply_markup.inline_keyboard
    )
    is_guest_bot_reply = (
        getattr(message, "guest_bot_caller_user", None) is not None
        or getattr(message, "guest_bot_caller_chat", None) is not None
    )
    reason = None
    if message.via_bot:
        reason = "via_bot"
    elif is_guest_bot_reply:
        reason = "guest_bot"
    elif has_inline_buttons and sender_username != "benny_captcha_bot":
        reason = "inline_keyboard"
    if reason:
        await delete_rule_message(message, matched_rule=f"message_signal:{reason}", content=content)
        if is_guest_bot_reply:
            await delete_guest_bot_trigger(client, message)
        logging.warning("potential spam message detected (%s)", reason)
        return True

    blacklist_id = [int(i) for i in os.getenv("BLACKLIST_ID", "").split(",") if i]
    blacklist_name = [i for i in os.getenv("BLACKLIST_NAME", "").split(",") if i]
    blacklist_emoji = [i for i in os.getenv("BLACKLIST_EMOJI", "").split(",") if i]
    blacklist_sticker = [i for i in os.getenv("BLACKLIST_STICKER", "").split(",") if i]
    blacklist_message = [i for i in os.getenv("BLACKLIST_MESSAGE", "").split(",") if i]

    # 纯本地关键词检查必须先于 Sticker、Bio 等网络请求，保持命中后立即删除。
    for msg in blacklist_message:
        if keyword_hit(msg, user_message):
            await delete_rule_message(message, matched_rule=f"blacklist_message:{msg}", content=content)
            return True

    sender_id = getattr(message.from_user, "id", None) or getattr(message.chat, "id", None)
    forward_id = getattr(message.forward_origin, "id", None)
    forward_title = getattr(message.forward_origin, "title", "")
    forward_type = getattr(message.forward_origin, "type", "")
    user_sticker = None
    is_ban = False
    if message.sticker:
        user_sticker = message.sticker.set_name
        sticker_set = await app.invoke(
            raw_functions.messages.GetStickerSet(
                stickerset=raw_types.InputStickerSetShortName(short_name=message.sticker.set_name),
                hash=0,
            )
        )
        if len(sticker_set.packs) == 1 or "点击直达" in sticker_set.set.title:
            logging.info("spam sticker detected")
            sticker_reason = "single_pack" if len(sticker_set.packs) == 1 else "click_through_title"
            await delete_rule_message(
                message,
                matched_rule=f"sticker_heuristic:{sticker_reason}",
                content=content,
            )
            return True

    chat = await app.get_chat(message.from_user.id)
    direct_rule = None
    if message.contact:
        direct_rule = "contact"
    elif user_message.startswith("https://t.me/+"):
        direct_rule = "invite_link_message"
    elif "t.me/+" in (chat.bio or ""):
        direct_rule = "invite_link_bio"
    elif user_sticker in blacklist_sticker:
        direct_rule = f"blacklist_sticker:{user_sticker}"
    if direct_rule is not None:
        await delete_rule_message(
            message,
            matched_rule=direct_rule,
            bio=chat.bio or None,
            bio_state="present" if chat.bio else "empty",
            content=content,
        )
        logging.warning("potential spam message detected (%s)", direct_rule.split(":", 1)[0])
        # just delete the message
        return True

    try:
        logging.info("Checking blacklist emojis...")
        # don't know why from_user cound be None
        # captcha-1  |     emoji_id = getattr(message.from_user.emoji_status, "custom_emoji_id", None)
        # captcha-1  | AttributeError: 'NoneType' object has no attribute 'emoji_status'
        emoji_id = getattr(message.from_user.emoji_status, "custom_emoji_id", None)
    except AttributeError:
        emoji_id = None
    emoji_set = None
    matched_rules = []
    if emoji_id:
        emoji_set = await app.get_custom_emoji_stickers([emoji_id])
    if emoji_set and emoji_set[0].set_name in blacklist_emoji:
        is_ban = True
        matched_rules.append(f"blacklist_emoji:{emoji_set[0].set_name}")

    logging.info("Checking blacklist names...")
    for name in blacklist_name:
        if keyword_hit(name, forward_title) and message.document and forward_type == enums.ChatType.CHANNEL:
            is_ban = True
            matched_rules.append(f"blacklist_name:{name}")
            break
        if (
            keyword_hit(name, getattr(message.from_user, "username", ""))
            or keyword_hit(name, getattr(message.from_user, "first_name", ""))
            or keyword_hit(name, getattr(message.from_user, "last_name", ""))
        ):
            is_ban = True
            matched_rules.append(f"blacklist_name:{name}")
            break

    logging.info("Checking blacklist forward ids...")
    if sender_id in blacklist_id or forward_id in blacklist_id:
        is_ban = True
        matched_rules.append("blacklist_id")

    if is_ban:
        logging.info("prepress bad user")
        await delete_rule_message(
            message,
            matched_rule="|".join(matched_rules),
            bio=chat.bio or None,
            bio_state="present" if chat.bio else "empty",
            content=content,
        )
        await ban_user(message.chat.id, sender_id)
    else:
        logging.info("Good user and message")
        service = spam_capture_service
        if (service is not None and user_message.strip()
                and spamdet_config.records_group(getattr(message.chat, "id", None))):
            try:
                # 普通群消息只提取现有字段，不在 Handler 中等待任何网络操作。
                service.submit(
                    sample_from_message(
                        message,
                        source="group",
                        bio=chat.bio or None,
                        bio_state="present" if chat.bio else "empty",
                        content=content,
                    )
                )
            except Exception as exc:
                logging.warning("普通群消息采集失败（%s）", type(exc).__name__)
    return is_ban


@app.on_start()
async def startup(client):
    global spam_capture_service, spam_d1_client, spam_review_service, spam_inference_service
    if spamdet_config.enabled:
        assert spamdet_config.admin_id is not None
        spam_d1_client = D1Client.from_config(spamdet_config)
        spam_review_service = ReviewService(
            client,
            spam_d1_client,
            admin_id=spamdet_config.admin_id,
        )
        spam_inference_service = InferenceService.from_path(
            str(spamdet_config.model_path),
            max_concurrency=spamdet_config.inference_concurrency,
        )
        spam_capture_service = CaptureService(
            client,
            spam_d1_client,
            spam_review_service,
            inference=spam_inference_service,
            queue_size=spamdet_config.capture_queue_size,
            worker_count=spamdet_config.capture_workers,
            bio_timeout=spamdet_config.bio_timeout_seconds,
            bio_cache_ttl=spamdet_config.bio_cache_ttl_seconds,
            bio_cache_size=spamdet_config.bio_cache_size,
            shutdown_timeout=spamdet_config.capture_shutdown_timeout_seconds,
        )
        spam_capture_service.start()
        logging.info(
            "Spam 规则样本采集已启动，模型可用性=%s",
            spam_inference_service.status.available,
        )
    scheduler.add_job(check_idle_verification, "interval", seconds=15)
    scheduler.start()
    logging.info("Scheduler started!")


@app.on_stop()
async def shutdown(client):
    global spam_capture_service, spam_d1_client, spam_review_service, spam_inference_service
    if spam_capture_service is not None:
        await spam_capture_service.aclose()
        spam_capture_service = None
    spam_review_service = None
    spam_inference_service = None
    if spam_d1_client is not None:
        await spam_d1_client.aclose()
        spam_d1_client = None


if __name__ == "__main__":
    app.run()
