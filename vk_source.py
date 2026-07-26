# -*- coding: utf-8 -*-
"""
Чтение VK-беседы через сообщество-бота.

Важно: обычный Bots Long Poll в беседе отдаёт боту ТОЛЬКО упоминания,
если сообщество не админ беседы (а новый VK не даёт назначить бота админом).
Поэтому читаем беседу опросом истории: messages.getHistory токеном сообщества.
Сообщество — участник беседы, значит видит всю переписку, без упоминаний.
"""
import os
import time
import datetime
import logging

import config
import db
from filters import is_news, special_reason

VK_TOKEN = os.environ.get("VK_GROUP_TOKEN", "")
POLL_EVERY_SEC = 60          # как часто опрашивать беседу


def _peer_id() -> int:
    return 2000000000 + config.VK_CHAT_ID


def _enqueue(text: str, mid):
    if not is_news(text):
        return
    reason = special_reason(text)
    kind = "special" if reason else "digest"
    db.add_to_queue("vk", None, None, text, kind, reason or "")
    logging.info("VK новость в очереди (%s): %r", kind, text[:60])


def poll_vk():
    """Опрос истории беседы. Запускать в отдельном потоке."""
    if not VK_TOKEN or not config.VK_CHAT_ID:
        logging.info("VK выключен (нет VK_GROUP_TOKEN / VK_CHAT_ID)")
        return

    import vk_api

    vk = vk_api.VkApi(token=VK_TOKEN).get_api()
    peer = _peer_id()

    # Первый проход — только запомнить текущие сообщения, чтобы не вывалить
    # всю старую историю в канал. Но лишь если раньше VK-сообщений не видели.
    # (старьё заливается отдельно через import_vk.py)
    prime = db.max_seen_id("vk") == 0
    logging.info("VK опрос беседы запущен (peer=%s, prime=%s)", peer, prime)

    while True:
        try:
            resp = vk.messages.getHistory(peer_id=peer, count=50)
            items = resp.get("items", [])
            for m in reversed(items):            # старые -> новые
                mid = m["id"]
                if db.is_seen("vk", mid):
                    continue
                db.mark_seen("vk", mid)
                if prime:
                    continue                     # первый проход: только помечаем
                _enqueue(m.get("text", ""), mid)
            prime = False
        except Exception as e:
            logging.exception("VK poll error: %s", e)
        time.sleep(POLL_EVERY_SEC)


def import_vk_history():
    """Разовая заливка старых сообщений беседы (с VK_START_DATE)."""
    if not VK_TOKEN or not config.VK_CHAT_ID:
        print("VK не настроен (VK_GROUP_TOKEN / VK_CHAT_ID)")
        return

    import vk_api

    vk = vk_api.VkApi(token=VK_TOKEN).get_api()
    start_ts = datetime.datetime.fromisoformat(config.VK_START_DATE).timestamp()
    peer = _peer_id()
    offset, added = 0, 0

    while True:
        resp = vk.messages.getHistory(peer_id=peer, count=200, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        stop = False
        for m in items:
            if m["date"] < start_ts:
                stop = True
                break
            mid = m["id"]
            if db.is_seen("vk", mid):
                continue
            db.mark_seen("vk", mid)
            text = m.get("text", "")
            if not is_news(text):
                continue
            reason = special_reason(text)
            kind = "special" if reason else "digest"
            db.add_to_queue("vk", None, None, text, kind, reason or "")
            added += 1
        if stop:
            break
        offset += 200

    print(f"VK импортировано: {added}")
