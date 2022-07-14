#!/usr/local/bin/python3
# coding: utf-8

# JoinGroup - main.py
# 7/14/22 18:16
#

__author__ = "Benny <benny.think@gmail.com>"

import os
import logging
import time

import redis
from pyrogram import Client, filters, types, enums
from captcha.image import ImageCaptcha
import re
import string
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
APP_ID = os.getenv("APP_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS = os.getenv("REDIS", "localhost")
app = Client("group", APP_ID, API_HASH, bot_token=BOT_TOKEN,
             proxy={"scheme": "socks5", "hostname": "127.0.0.1", "port": 1080}
             )
redis_client = redis.StrictRedis(host=REDIS, decode_responses=True)
image = ImageCaptcha()
predefined_str = re.sub(r"[1l0oOI]", "", string.ascii_letters + string.digits)


def generate_char():
    return "".join([random.choice(predefined_str) for _ in range(4)])


@app.on_message(filters.new_chat_members)
async def hello(client: "Client", message: "types.Message"):
    from_user_id = message.from_user.id
    name = message.from_user.first_name
    await restrict_user(message.chat.id, from_user_id)
    chars = generate_char()
    data = image.generate(chars)
    data.name = f"{message.id}-captcha.png"

    user_button = []
    for _ in range(5):
        fake_char = generate_char()
        user_button.append(types.InlineKeyboardButton(text=fake_char, callback_data=f"{fake_char}_{from_user_id}"))
    user_button[random.randint(0, len(user_button) - 1)] = \
        types.InlineKeyboardButton(text=chars, callback_data=f"{chars}_{from_user_id}")
    markup = types.InlineKeyboardMarkup(
        [
            user_button,
            [
                types.InlineKeyboardButton("Approve", callback_data=f"Approve_{from_user_id}"),
                types.InlineKeyboardButton("Deny", callback_data=f"Deny_{from_user_id}"),
            ]
        ]
    )

    bot_message = await message.reply_photo(data,
                                            caption=f"Hello [{name}](tg://user?id={from_user_id}), "
                                                    f"please verify by clicking correct buttons",
                                            reply_markup=markup,
                                            reply_to_message_id=message.id
                                            )

    group_id = message.chat.id
    message_id = bot_message.id
    redis_client.hset(group_id, message_id, chars)
    # delete service message
    await message.delete()


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
    logging.info("User %s click %s, correct answer is %s", click_user, user_result, correct_result)

    if user_result == correct_result:
        await callback_query.answer("Welcome!")
        await un_restrict_user(group_id, joining_user)
    else:
        await callback_query.answer("Wrong answer")
        await ban_user(group_id, joining_user)

    redis_client.hdel(group_id, msg_id)
    await callback_query.message.delete()


async def restrict_user(gid, uid):
    await app.restrict_chat_member(gid, uid, types.ChatPermissions())


async def ban_user(gid, uid):
    _ = await app.ban_chat_member(gid, uid)
    await _.delete()

    # only for dev
    time.sleep(20)
    logging.info("Remove user from banning list")
    await app.unban_chat_member(gid, uid)


async def un_restrict_user(gid, uid):
    await app.restrict_chat_member(gid, uid,
                                   types.ChatPermissions(
                                       can_send_messages=True,
                                       can_send_media_messages=True,
                                       can_send_other_messages=True,
                                       can_send_polls=True,
                                       can_add_web_page_previews=True,
                                       can_invite_users=True,
                                       can_change_info=False,
                                       can_pin_messages=False)
                                   )


if __name__ == '__main__':
    app.run()
