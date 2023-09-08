#!/usr/bin/env python3
# coding: utf-8

# JoinGroup - main.py
# 7/14/22 18:16
#

__author__ = "Benny <benny.think@gmail.com>"

import contextlib
import logging
import os
import random
import re
import string
import time

import redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from captcha.image import ImageCaptcha
from pyrogram import Client, enums, filters, types

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("apscheduler.executors.default").propagate = False

APP_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS = os.getenv("REDIS", "localhost")
app = Client("captchabot", APP_ID, API_HASH, bot_token=BOT_TOKEN)
redis_client = redis.StrictRedis(host=REDIS, decode_responses=True, db=8)
image = ImageCaptcha()
PREDEFINED_STR = re.sub(r"[1l0oOI]", "", string.ascii_letters + string.digits)
IDLE_SECONDS = 2 * 60


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
        user_button.append(
            types.InlineKeyboardButton(
                text=fake_char, callback_data=f"{fake_char}_{from_user_id}"
            )
        )

    user_button[random.randint(0, len(user_button) - 1)] = types.InlineKeyboardButton(
        text=chars,
        callback_data=f"{chars}_{from_user_id}",
    )

    user_button = [user_button[i: i + 3] for i in range(0, len(user_button), 3)]
    markup = types.InlineKeyboardMarkup(
        [
            user_button[0],
            user_button[1],
            [
                types.InlineKeyboardButton(
                    "Approve", callback_data=f"Approve_{from_user_id}"
                ),
                types.InlineKeyboardButton(
                    "Deny", callback_data=f"Deny_{from_user_id}"
                ),
            ],
        ]
    )

    bot_message = await message.reply_photo(
        data,
        caption=f"Hello [{name}](tg://user?id={from_user_id}), "
                f"please verify by clicking correct buttons in 2 minutes",
        reply_markup=markup,
        reply_to_message_id=message.id,
    )

    group_id = message.chat.id
    message_id = bot_message.id
    redis_client.hset(group_id, message_id, chars)
    # delete service message
    await message.delete()
    redis_client.hset("queue", f"{group_id},{message_id}", int(time.time()))


@app.on_callback_query(filters.regex(r"Approve_.*"))
async def admin_approve(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id
    join_user_id = callback_query.data.split("_")[1]
    # Get administrators
    administrators = []
    async for m in app.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS
    ):
        administrators.append(m.user.id)
    if from_user_id in administrators:
        await callback_query.answer("Approved")
        await callback_query.message.delete()
        await un_restrict_user(chat_id, join_user_id)
    else:
        await callback_query.answer("You are not administrator")

    invalid_queue(f"{chat_id},{callback_query.message.id}")


@app.on_callback_query(filters.regex(r"Deny_.*"))
async def admin_deny(client: "Client", callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    from_user_id = callback_query.from_user.id  # this is admin
    join_user_id = callback_query.data.split("_")[1]

    administrators = []
    async for m in app.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS
    ):
        administrators.append(m.user.id)
    if from_user_id in administrators:
        await callback_query.answer("Denied")
        await callback_query.message.delete()
        await ban_user(chat_id, join_user_id)
    else:
        await callback_query.answer("You are not administrator")

    invalid_queue(f"{chat_id},{callback_query.message.id}")


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
    correct_result = redis_client.hget(group_id, msg_id)
    user_result = callback_query.data.split("_")[0]
    logging.info(
        "User %s click %s, correct answer is %s",
        click_user,
        user_result,
        correct_result,
    )

    if user_result == correct_result:
        await callback_query.answer("Welcome!")
        await un_restrict_user(group_id, joining_user)
    else:
        await callback_query.answer("Wrong answer")
        await ban_user(group_id, joining_user)

    redis_client.hdel(group_id, msg_id)
    logging.info("Deleting inline button...")
    await callback_query.message.delete()
    invalid_queue(f"{group_id},{msg_id}")


async def restrict_user(gid, uid):
    await app.restrict_chat_member(gid, uid, types.ChatPermissions())


async def ban_user(gid, uid):
    _ = await app.ban_chat_member(gid, uid)

    # only for dev
    if os.uname().nodename == "BennyのMBP":
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


def invalid_queue(gid_uid):
    logging.info("Invalidating queue %s", gid_uid)
    redis_client.hdel("queue", gid_uid)


async def check_idle_verification():
    for gu, ts in redis_client.hgetall("queue").items():
        time.sleep(random.random())
        if time.time() - int(ts) > IDLE_SECONDS:
            logging.info("Idle verification for %s", gu)
            with contextlib.suppress(Exception):
                await delete_captcha(gu)


async def delete_captcha(gu):
    invalid_queue(gu)
    gu_int = [int(i) for i in gu.split(",")]
    msg = await app.get_messages(*gu_int)
    target_user = msg.caption_entities[0].user.id
    await ban_user(gu_int[0], target_user)
    await msg.delete()


@app.on_message(filters.group & ~filters.left_chat_member)
async def group_message_handler(client: "Client", message: "types.Message"):
    blacklist_id = [int(i) for i in os.getenv("BLACKLIST_ID", "").split(",") if i]
    blacklist_name = [i for i in os.getenv("BLACKLIST_NAME", "").split(",") if i]
    sender_id = message.from_user.id
    forward_id = getattr(message.forward_from_chat, "id", None)
    forward_title = getattr(message.forward_from_chat, "title", "")
    forward_type = getattr(message.forward_from_chat, "type", "")
    is_ban = False

    logging.info("Checking blacklist...")
    if message.from_user.emoji_status.custom_emoji_id == "5109819404909019795":
        is_ban = True
    for bn in blacklist_name:
        if (
                bn.lower() in forward_title.lower()
                and message.document
                and forward_type == enums.ChatType.CHANNEL
        ):
            is_ban = True
            break
        if (
                bn.lower() in (message.from_user.username or "")
                or bn.lower() in (message.from_user.first_name or "")
                or bn.lower() in (message.from_user.last_name or "")
        ):
            is_ban = True
            break

    if sender_id in blacklist_id or forward_id in blacklist_id:
        is_ban = True

    if is_ban:
        logging.info("Sender %s, forward %s is in blacklist", sender_id, forward_id)
        await message.delete()
        await ban_user(message.chat.id, sender_id)
    return is_ban


if __name__ == "__main__":
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_idle_verification, "interval", minutes=1)
    scheduler.start()
    app.run()
