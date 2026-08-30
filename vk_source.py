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

log = logging.getLogger(__name__)

VK_TOKEN = os.environ.get("VK_GROUP_TOKEN", "")

# Пауза между переподключениями Long Poll: 5с -> 10 -> 20 ... но не больше 5 минут.
BACKOFF_START = 5
BACKOFF_MAX = 300

# убрать префикс-упоминание вида [club240473090|@club240473090]
_mention_re = re.compile(r"\[(?:club|public|id)\d+\|[^\]]*\]\s*")


def _peer_id() -> int:
    return 2000000000 + config.VK_CHAT_ID


def vk_enabled() -> bool:
    """VK включён, только если есть токен, id сообщества и ОСМЫСЛЕННЫЙ id беседы.

    VK_CHAT_ID <= 1 — это дефолт-заглушка из config.py, а не настоящая беседа
    (peer_id 2000000001 почти наверняка чужой/несуществующий чат). Стартовать
    с ним нельзя: поток будет вечно падать и засорять логи.
    """
    if not VK_TOKEN:
        log.info("VK отключён: нет VK_GROUP_TOKEN")
        return False
    if not config.VK_GROUP_ID:
        log.info("VK отключён: VK_GROUP_ID=0")
        return False
    if config.VK_CHAT_ID <= 1:
        log.info("VK отключён: VK_CHAT_ID=%s (<=1 — это заглушка, а не реальная беседа). "
                 "Открой беседу в vk.com/im, возьми N из sel=cN и задай VK_CHAT_ID.",
                 config.VK_CHAT_ID)
        return False
    return True


def _enqueue(text: str, mid):
    peer = _peer_id()
    if mid is not None and db.is_seen("vk", peer, mid):
        return
    if mid is not None:
        db.mark_seen("vk", peer, mid)
    text = _mention_re.sub("", text or "").strip()
    if not is_news(text):
        return
    reason = special_reason(text)
    kind = "special" if reason else "digest"
    db.add_to_queue("vk", None, None, text, kind, reason or "")
    log.info("VK новость в очереди (%s): %r", kind, text[:60])


def listen_vk():
    """Bots Long Poll сообщества. Запускать в отдельном потоке."""
    if not vk_enabled():
        return

    import vk_api
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

    target = _peer_id()
    backoff = BACKOFF_START

    while True:
        try:
            session = vk_api.VkApi(token=VK_TOKEN)
            lp = VkBotLongPoll(session, group_id=config.VK_GROUP_ID)
            log.info("VK Long Poll запущен (target peer=%s)", target)
            backoff = BACKOFF_START      # успешно подключились — сбрасываем паузу

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
                if peer != target:
                    continue
                log.info("VK получено: peer=%s mid=%s text=%r", peer, mid, (text or "")[:80])
                _enqueue(text, mid)

        except Exception as e:
            # Таймаут Long Poll — штатное явление, он случается постоянно.
            # Раньше он валился сюда полным трейсбеком и ежедневно засорял логи.
            if _is_timeout(e):
                log.warning("VK Long Poll: таймаут (%s), переподключаюсь", type(e).__name__)
                time.sleep(BACKOFF_START)
                continue
            log.warning("VK Long Poll: %s: %s — переподключаюсь через %dс",
                        type(e).__name__, e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


def _is_timeout(exc) -> bool:
    """Сетевой таймаут/обрыв чтения — не ошибка, а обычная жизнь Long Poll."""
    name = type(exc).__name__
    if name in ("ReadTimeout", "ReadTimeoutError", "ConnectionError", "Timeout",
                "ConnectTimeout", "ChunkedEncodingError"):
        return True
    try:
        import requests.exceptions as rex
        return isinstance(exc, (rex.ReadTimeout, rex.ConnectTimeout, rex.ConnectionError))
    except ImportError:
        return False


def import_vk_history():
    """Разовая заливка старых сообщений беседы (работает, только если бот — админ беседы)."""
    if not vk_enabled():
        print("VK не настроен (VK_GROUP_TOKEN / VK_GROUP_ID / VK_CHAT_ID)")
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
            if db.is_seen("vk", peer, mid):
                continue
            db.mark_seen("vk", peer, mid)
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
