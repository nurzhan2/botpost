# -*- coding: utf-8 -*-
"""
Обычный Telegram-бот (aiogram 3.x).

Бот — АДМИН в канале-источнике @semyadruj и в канале-получателе @Friendl_family23.
Ловит новые посты источника, фильтрует, копит и публикует.

Ограничение Bot API: бот видит только НОВЫЕ посты (после добавления в админы).
Старые новости заливаются отдельно через import_history.py.
"""
import os

from aiogram import Bot, Dispatcher
from aiogram.types import Message

import config
import db
from filters import is_news, special_reason
from digest import build_digest_messages

bot = Bot(os.environ["BOT_TOKEN"])
dp = Dispatcher()


def _is_source(message: Message) -> bool:
    username = (message.chat.username or "").lower()
    if config.SOURCE_TG_USERNAME and username == config.SOURCE_TG_USERNAME.lower():
        return True
    if config.SOURCE_TG_CHAT_ID and message.chat.id == config.SOURCE_TG_CHAT_ID:
        return True
    return False


@dp.channel_post()
async def on_channel_post(message: Message):
    """Новый пост в источнике -> фильтр -> очередь."""
    if not _is_source(message):
        return  # это не наш источник (напр. эхо из канала-получателя)

    if db.is_seen("tg", message.message_id):
        return
    db.mark_seen("tg", message.message_id)

    text = message.text or message.caption or ""
    if not is_news(text):          # спам/реклама/фото без текста — мимо
        return

    reason = special_reason(text)
    kind = "special" if reason else "digest"
    db.add_to_queue("tg", message.chat.id, message.message_id, text, kind, reason or "")


async def post_special():
    """Особые новости — отдельными постами. Если есть оригинал — копируем с фото."""
    for qid, chat_id, msg_id, text, _reason, _created in db.pending("special"):
        try:
            if chat_id and msg_id:
                # copy_message переносит фото + подпись без пометки «переслано»
                await bot.copy_message(config.TARGET_TG_CHANNEL, from_chat_id=chat_id, message_id=msg_id)
            else:
                await bot.send_message(config.TARGET_TG_CHANNEL, text.strip(), disable_web_page_preview=True)
        except Exception:
            await bot.send_message(config.TARGET_TG_CHANNEL, text.strip(), disable_web_page_preview=True)
        db.mark_posted([qid])


async def post_digest():
    """Дайджест: когда накопилось DIGEST_SIZE, либо старьё висит дольше FLUSH_AFTER_DAYS."""
    n = db.count_pending("digest")
    if n == 0:
        return
    age = db.oldest_pending_age_days("digest")
    ready = n >= config.DIGEST_SIZE or (age is not None and age >= config.FLUSH_AFTER_DAYS)
    if not ready:
        return

    rows = db.pending("digest", limit=config.DIGEST_SIZE)
    ids = [r[0] for r in rows]
    items = [r[3] for r in rows]

    for message_text in build_digest_messages(items):
        await bot.send_message(config.TARGET_TG_CHANNEL, message_text, disable_web_page_preview=True)

    db.mark_posted(ids)
