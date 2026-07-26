# -*- coding: utf-8 -*-
"""
Чтение VK-беседы через Bots Long Poll сообщества.

Чтобы бот получал ВСЕ сообщения беседы (а не только упоминания),
сообщество должно быть АДМИНИСТРАТОРОМ беседы — это требование VK.
"""
import os
import re
import time
import datetime
import logging

import config
import db
from filters import is_news, special_reason

VK_TOKEN = os.environ.get("VK_GROUP_TOKEN", "")

# убрать префикс-упоминание вида [club240473090|@club240473090]
_mention_re = re.compile(r"\[(?:club|public|id)\d+\|[^\]]*\]\s*")


def _peer_id() -> int:
    return 2000000000 + config.VK_CHAT_ID


def _enqueue(text: str, mid):
    if mid is not None and db.is_seen("vk", mid):
        return
    if mid is not None:
        db.mark_seen("vk", mid)
    text = _mention_re.sub("", text or "").strip()
    if not is_news(text):
        return
    reason = special_reason(text)
    kind = "special" if reason else "digest"
    db.add_to_queue("vk", None, None, text, kind, reason or "")
    logging.info("VK новость в очереди (%s): %r", kind, text[:60])


def listen_vk():
    """Bots Long Poll сообщества. Запускать в отдельном потоке."""
    if not VK_TOKEN or not config.VK_GROUP_ID:
        logging.info("VK выключен (нет VK_GROUP_TOKEN / VK_GROUP_ID)")
        return

    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

    session = vk_api.VkApi(token=VK_TOKEN)
    lp = VkBotLongPoll(session, group_id=config.VK_GROUP_ID)
    target = _peer_id() if config.VK_CHAT_ID else None
    logging.info("VK Long Poll запущен (target peer=%s)", target)

    while True:
        try:
            for event in lp.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue
                msg = getattr(event, "message", None)
                if msg is None:
                    obj = event.object
                    msg = obj.get("message", obj) if hasattr(obj, "get") else obj
                if hasattr(msg, "get"):
                    peer, text, mid = msg.get("peer_id"), msg.get("text", ""), msg.get("id")
                else:
                    peer, text, mid = msg.peer_id, msg.text, msg.id
                if target and peer != target:
                    continue
                logging.info("VK получено: peer=%s mid=%s text=%r", peer, mid, (text or "")[:80])
                _enqueue(text, mid)
        except Exception as e:
            logging.exception("VK Long Poll error: %s", e)
            time.sleep(5)


def import_vk_history():
    """Разовая заливка старых сообщений беседы (работает, только если бот — админ беседы)."""
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
            text = _mention_re.sub("", m.get("text", "")).strip()
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
