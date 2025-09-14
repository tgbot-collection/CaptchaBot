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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("apscheduler.executors.default").propagate = False

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


def generate_char():
    return "".join([random.choice(PREDEFINED_STR) for _ in range(5)])


@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: "Client", message: "types.Message"):
    logging.info("Welcome to Captcha Bot")
    await message.reply_text("Hello! Add me to a group and make me admin!", quote=True)


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
    # redis data structure: name: group_id,chat_id  k-v: created:timestamp, message_id:id, captcha:chars, status:deleted
    name = f"{group_id},{from_user_id}"
    mapping = {"created": str(time.time()), "message_id": str(message_id), "captcha": chars, "deleted": "false"}
    await redis_client.hset(name, mapping=mapping)
    #  deleting service message and ignoring error
    with contextlib.suppress(Exception):
        await message.delete()
    # TODO sleep and then delete or maybe create_task
    # await asyncio.sleep(30)
    # await bot_message.delete()


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
    else:
        await callback_query.answer("You are not administrator")

    await invalid_queue(f"{chat_id},{join_user_id}")


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
    else:
        await callback_query.answer("You are not administrator")

    await invalid_queue(f"{chat_id},{join_user_id}")


# TODO broad event listener
@app.on_callback_query()
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
    with contextlib.suppress(Exception):
        await app.restrict_chat_member(
            gid,
            uid,
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
    # await redis_client.delete(gid_uid)
    await redis_client.hset(gid_uid, mapping={"deleted": "true"})


async def check_idle_verification():
    items = await redis_client.keys("*")
    value = None
    for gid_uid in items:
        try:
            value = await redis_client.hgetall(gid_uid)
            if value.get("deleted") == "true":
                continue
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
    # count = 0
    # while True:
    #     try:
    #         logging.info("preparing to delete captcha message %s %s in group %s", from_user_id, message_id, group_id)
    #         count += 1
    #         msg = await app.get_messages(group_id, message_id)
    #         if msg.empty or count >= 5:
    #             break
    #         await msg.delete()
    #         await asyncio.sleep(1)
    #     except Exception as e:
    #         logging.error("Failed to delete message %s in group %s: %s", message_id, group_id, e)
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


# only group incoming message, ignore service message
@app.on_message(filters.group & filters.incoming & ~filters.service)
@app.on_edited_message(filters.group & filters.incoming & ~filters.service)
async def group_message_preprocess(client: "Client", message: "types.Message"):
    blacklist_id = [int(i) for i in os.getenv("BLACKLIST_ID", "").split(",") if i]
    blacklist_name = [i for i in os.getenv("BLACKLIST_NAME", "").split(",") if i]
    blacklist_emoji = [i for i in os.getenv("BLACKLIST_EMOJI", "").split(",") if i]
    blacklist_sticker = [i for i in os.getenv("BLACKLIST_STICKER", "").split(",") if i]
    blacklist_message = [i for i in os.getenv("BLACKLIST_MESSAGE", "").split(",") if i]

    sender_id = getattr(message.from_user, "id", None) or getattr(message.chat, "id", None)
    forward_id = getattr(message.forward_origin, "id", None)
    forward_title = getattr(message.forward_origin, "title", "")
    forward_type = getattr(message.forward_origin, "type", "")
    user_message = message.text or ""
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
            logging.info("spam sticker detected:%s", sender_id)
            await message.delete()
            return True

    if (
        message.via_bot
        or message.reply_markup
        or user_message.startswith("https://t.me/+")
        or user_sticker in blacklist_sticker
    ):
        await message.delete()
        logging.warning("potential spam message detected: %s", user_message)
        # just delete the message
        return True

    for msg in blacklist_message:
        if keyword_hit(msg, user_message):
            await message.delete()
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
    if emoji_id:
        emoji_set = await app.get_custom_emoji_stickers([emoji_id])
    if emoji_set and emoji_set[0].set_name in blacklist_emoji:
        is_ban = True

    logging.info("Checking blacklist names...")
    for name in blacklist_name:
        if keyword_hit(name, forward_title) and message.document and forward_type == enums.ChatType.CHANNEL:
            is_ban = True
            break
        if (
            keyword_hit(name, getattr(message.from_user, "username", ""))
            or keyword_hit(name, getattr(message.from_user, "first_name", ""))
            or keyword_hit(name, getattr(message.from_user, "last_name", ""))
        ):
            is_ban = True
            break

    logging.info("Checking blacklist forward ids...")
    if sender_id in blacklist_id or forward_id in blacklist_id:
        is_ban = True

    if is_ban:
        logging.info("prepress bad user: %s", sender_id)
        await message.delete()
        await ban_user(message.chat.id, sender_id)
    else:
        logging.info("Good user and message: %s", sender_id)
    return is_ban


@app.on_start()
async def startup(client):
    scheduler.add_job(check_idle_verification, "interval", seconds=15)
    scheduler.start()
    logging.info("Scheduler started!")


if __name__ == "__main__":
    app.run()
