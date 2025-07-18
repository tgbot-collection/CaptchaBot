#!/usr/bin/env python3
# coding: utf-8


__author__ = "Benny <benny.think@gmail.com>"

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
from pyrogram.errors.exceptions.forbidden_403 import ChatAdminRequired
from retry import retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("apscheduler.executors.default").propagate = False

APP_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS = os.getenv("REDIS", "localhost")
app = Client("captchabot", APP_ID, API_HASH, bot_token=BOT_TOKEN)
redis_client = aioredis.StrictRedis(host=REDIS, decode_responses=True, db=8)
image = ImageCaptcha()
PREDEFINED_STR = re.sub(r"[1l0oOI]", "", string.ascii_letters + string.digits)
IDLE_SECONDS = 2 * 60
scheduler = AsyncIOScheduler()


def generate_char():
    return "".join([random.choice(PREDEFINED_STR) for _ in range(5)])


@app.on_message(filters.command(["start", "help"]))
async def start_handler(client: "Client", message: "types.Message"):
    logging.info("Welcome to Captcha Bot")
    await message.reply_text("Hello! Add me to a group and make me admin!", quote=True)


@app.on_message(filters.new_chat_members)
async def new_chat(client: "Client", message: "types.Message"):
    logging.info("new chat member: %s", message.from_user)
    if await group_message_handler(client, message):
        # bad user ban directly
        return
    from_user_id = message.from_user.id
    name = message.from_user.first_name
    await restrict_user(message.chat.id, from_user_id)
    chars = generate_char()
    data = image.generate(chars)
    data.name = f"{message.id}-captcha.png"

    user_button = []
    for _ in range(6):
        fake_char = generate_char()
        user_button.append(types.InlineKeyboardButton(text=fake_char, callback_data=f"{fake_char}_{from_user_id}"))

    user_button[random.randint(0, len(user_button) - 1)] = types.InlineKeyboardButton(
        text=chars,
        callback_data=f"{chars}_{from_user_id}",
    )

    user_button = [user_button[i : i + 3] for i in range(0, len(user_button), 3)]
    markup = types.InlineKeyboardMarkup(
        [
            user_button[0],
            user_button[1],
            [
                types.InlineKeyboardButton("Approve", callback_data=f"Approve_{from_user_id}"),
                types.InlineKeyboardButton("Deny", callback_data=f"Deny_{from_user_id}"),
            ],
        ]
    )
    bot_message = await client.send_photo(
        chat_id=message.chat.id,
        photo=data,
        caption=f"Hello [{name}](tg://user?id={from_user_id}), "
        f"please verify by clicking correct buttons in 2 minutes",
        reply_markup=markup,
    )

    group_id = message.chat.id
    message_id = bot_message.id
    await redis_client.hset(str(group_id), str(message_id), chars)
    # delete service message
    await message.delete()
    await redis_client.hset("queue", f"{group_id},{message_id}", str(time.time()))


@app.on_callback_query(filters.regex(r"Approve_.*"))
async def admin_approve(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id
    join_user_id = callback_query.data.split("_")[1]
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

    await invalid_queue(f"{chat_id},{callback_query.message.id}")


@app.on_callback_query(filters.regex(r"Deny_.*"))
async def admin_deny(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id  # this is admin
    join_user_id = callback_query.data.split("_")[1]

    administrators = []
    async for m in app.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
        administrators.append(m.user.id)
    if from_user_id in administrators:
        await callback_query.answer("Denied")
        await callback_query.message.delete()
        await ban_user(chat_id, join_user_id)
    else:
        await callback_query.answer("You are not administrator")

    await invalid_queue(f"{chat_id},{callback_query.message.id}")


# TODO broad event listener
@app.on_callback_query()
async def user_press(client: "Client", callback_query: types.CallbackQuery):
    click_user = callback_query.from_user.id
    joining_user = callback_query.data.split("_")[1]
    if str(click_user) != joining_user:
        await callback_query.answer("You are not the one who is joining")
        return

    group_id = callback_query.message.chat.id
    msg_id = callback_query.message.id
    correct_result = await redis_client.hget(str(group_id), str(msg_id))
    user_result = callback_query.data.split("_")[0]
    logging.info("User %s click %s, correct answer:%s", click_user, user_result, correct_result)

    if user_result == correct_result:
        await callback_query.answer("Welcome!")
        await un_restrict_user(group_id, joining_user)
    else:
        await callback_query.answer("Wrong answer")
        await ban_user(group_id, joining_user)

    await redis_client.hdel(str(group_id), str(msg_id))
    logging.info("Deleting inline button...")
    await callback_query.message.delete()
    await invalid_queue(f"{group_id},{msg_id}")


async def restrict_user(gid, uid):
    # this method may throw an error if bot is not admin, so we just ignore it
    try:
        await app.restrict_chat_member(gid, uid, types.ChatPermissions())
    except ChatAdminRequired:
        logging.error("Bot is not admin in group %s, cannot restrict user %s", gid, uid)


async def ban_user(gid, uid):
    _ = await app.ban_chat_member(gid, uid)

    # only for dev
    if os.getenv("MODE") == "dev":
        time.sleep(10)
        logging.info("Remove user from banning list")
        await app.unban_chat_member(gid, uid)


async def un_restrict_user(gid, uid):
    await app.restrict_chat_member(
        gid,
        uid,
        types.ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_send_polls=True,
            can_add_web_page_previews=True,
            can_invite_users=True,
            can_change_info=False,
            can_pin_messages=False,
        ),
    )


async def invalid_queue(gid_uid):
    await redis_client.hdel("queue", gid_uid)


async def check_idle_verification():
    for group_id, ts in (await redis_client.hgetall("queue")).items():
        time.sleep(random.random())
        if time.time() - float(ts) > IDLE_SECONDS:
            logging.info("Idle verification for %s", group_id)
            try:
                await delete_captcha(group_id)
            except:
                logging.error("error in deleting captcha %s", group_id, exc_info=True)


@retry(tries=3, delay=2)
async def delete_captcha(gu):
    await invalid_queue(gu)
    gu_int = [int(i) for i in gu.split(",")]
    msg = await app.get_messages(*gu_int)
    logging.info("message to be deleted: %s", msg)
    await msg.delete()
    target_user = msg.caption_entities[0].user.id
    await ban_user(gu_int[0], target_user)


@app.on_message(filters.group & filters.incoming)
async def group_message_handler(client: "Client", message: "types.Message"):
    blacklist_id = [int(i) for i in os.getenv("BLACKLIST_ID", "").split(",") if i]
    blacklist_name = [i for i in os.getenv("BLACKLIST_NAME", "").split(",") if i]
    blacklist_emoji = [i for i in os.getenv("BLACKLIST_EMOJI", "").split(",") if i]
    blacklist_sticker = [i for i in os.getenv("BLACKLIST_STICKER", "").split(",") if i]
    sender_id = getattr(message.from_user, "id", None) or getattr(message.chat, "id", None)
    forward_id = getattr(message.forward_from_chat, "id", None)
    forward_title = getattr(message.forward_from_chat, "title", "")
    forward_type = getattr(message.forward_from_chat, "type", "")
    user_message = message.text or ""
    user_sticker = None
    if message.sticker:
        user_sticker = message.sticker.set_name
    is_ban = False

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
    for bn in blacklist_name:
        if bn.lower() in forward_title.lower() and message.document and forward_type == enums.ChatType.CHANNEL:
            is_ban = True
            break
        if (
            bn.lower() in (message.from_user.username or "").lower()
            or bn.lower() in (message.from_user.first_name or "").lower()
            or bn.lower() in (message.from_user.last_name or "").lower()
        ):
            is_ban = True
            break

    logging.info("Checking blacklist forward ids...")
    if sender_id in blacklist_id or forward_id in blacklist_id:
        is_ban = True

    if is_ban:
        logging.info("Sender %s, forward %s is in blacklist", sender_id, forward_id)
        await message.delete()
        await ban_user(message.chat.id, sender_id)
    else:
        logging.info("Good user")
    return is_ban


@app.on_start()
async def startup(client):
    scheduler.add_job(check_idle_verification, "interval", minutes=1)
    scheduler.start()
    logging.info("Scheduler started!")


if __name__ == "__main__":
    app.run()
