# -*- coding: utf-8 -*-
"""
Обычный Telegram-бот (aiogram 3.x).

Бот — АДМИН в источнике @semyadruj и в канале-получателе @Friendl_family23.
Ловит новые посты источника, фильтрует, копит и публикует.

ВАЖНО про типы апдейтов:
источник @semyadruj — СУПЕРГРУППА (type=supergroup), а не канал. Супергруппа
шлёт `message`, канал шлёт `channel_post`. Раньше бот был подписан только на
channel_post, Telegram отбрасывал все посты источника у себя, и до фильтров
не доходило вообще ничего. Поэтому слушаем ОБА типа, а разбор у них общий.

Ограничение Bot API: бот видит только НОВЫЕ посты (после добавления в админы).
Старые новости заливаются отдельно через import_history.py.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import config
import db
from filters import is_news, special_reason, spam_hit
from digest import build_digest_messages

log = logging.getLogger(__name__)

bot = Bot(os.environ["BOT_TOKEN"])
dp = Dispatcher()

# ADMIN_IDS=123456789,987654321 — кому доступны /status и /testpost
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",")
    if x.strip().lstrip("-").isdigit()
}

# Планировщик прокидывается из main.py, нужен /status для «когда следующий запуск»
_scheduler = None


def set_scheduler(sched):
    global _scheduler
    _scheduler = sched


# --- буфер медиагрупп (альбомов) ---------------------------------------
# Альбом прилетает НЕСКОЛЬКИМИ апдейтами с общим media_group_id, и подпись
# есть только у одного из них — причём не обязательно у первого. Буфер общий
# для обоих путей (channel_post и message), ключ включает chat_id, чтобы
# media_group_id из разных чатов не смешались.
ALBUM_DEBOUNCE_SEC = 2.0
_album_buf = {}   # (chat_id, media_group_id) -> {"msgs": [Message], "task": Task}

# Счётчик исходов с момента старта процесса. Нужен именно в памяти, а не в БД:
# посты без маркера НЕ попадают в seen, поэтому по таблице seen нельзя понять
# «мы что-то видели, но ничего не поставили в очередь».
_stats = {"queued": 0, "spam": 0, "no_marker": 0, "empty_text": 0}


def _is_source(message: Message) -> bool:
    """Наш ли это чат-источник. При несовпадении логируем, ЧТО с ЧЕМ не сошлось."""
    # Числовой id — приоритетный и самый надёжный признак.
    if config.SOURCE_TG_CHAT_ID and message.chat.id == config.SOURCE_TG_CHAT_ID:
        return True

    username = (message.chat.username or "").lower()
    if config.SOURCE_TG_USERNAME and username == config.SOURCE_TG_USERNAME.lower():
        return True

    log.info(
        "not_source: пришло chat.id=%s chat.username=%r chat.type=%s title=%r; "
        "ожидали SOURCE_TG_CHAT_ID=%s SOURCE_TG_USERNAME=%r%s",
        message.chat.id, message.chat.username, message.chat.type,
        getattr(message.chat, "title", None),
        config.SOURCE_TG_CHAT_ID, config.SOURCE_TG_USERNAME,
        "" if config.SOURCE_TG_CHAT_ID else
        " (id не задан — матч только по username; задай SOURCE_TG_CHAT_ID в env)",
    )
    return False


def _ctx(message: Message, origin: str) -> str:
    text = message.text or message.caption or ""
    return ("origin=%s chat.id=%s chat.username=%s chat.type=%s msg_id=%s "
            "media_group_id=%s text=%r") % (
        origin, message.chat.id, message.chat.username, message.chat.type,
        message.message_id, message.media_group_id,
        text[:80].replace("\n", " "),
    )


async def ingest(message: Message, origin: str):
    """Единая точка входа для обоих типов апдейтов.

    origin — только для логов: 'channel_post' (канал) либо 'message' (супергруппа).
    """
    ctx = _ctx(message, origin)

    if not _is_source(message):
        log.info("skip: not_source | %s", ctx)
        return

    if message.media_group_id:
        _buffer_album(message, origin, ctx)
        return

    _handle(message, message.text or message.caption or "", [message.message_id], ctx)


def _buffer_album(message: Message, origin: str, ctx: str):
    """Накопить сообщение альбома и перевзвести таймер на разбор всей группы."""
    key = (message.chat.id, message.media_group_id)
    slot = _album_buf.setdefault(key, {"msgs": [], "task": None, "origin": origin})
    slot["msgs"].append(message)
    if slot["task"] is not None:
        slot["task"].cancel()
    slot["task"] = asyncio.create_task(_flush_album_later(key))
    log.info("album: буферизую (в группе %d) | %s", len(slot["msgs"]), ctx)


async def _flush_album_later(key):
    try:
        await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
    except asyncio.CancelledError:
        return   # пришло ещё одно фото группы — таймер перевзведён
    slot = _album_buf.pop(key, None)
    if not slot or not slot["msgs"]:
        return

    msgs = slot["msgs"]
    ids = [m.message_id for m in msgs]
    # Берём текст оттуда, где он есть; носителем считаем именно то сообщение
    # (его copy_message перенесёт с фото и подписью).
    carrier = next((m for m in msgs if (m.text or m.caption or "").strip()), msgs[0])
    text = carrier.text or carrier.caption or ""
    log.info("album: группа %s собрана, сообщений=%d, подпись у msg_id=%s",
             key[1], len(msgs), carrier.message_id)
    _handle(carrier, text, ids, _ctx(carrier, slot["origin"]))


def _handle(message: Message, text: str, all_ids: list, ctx: str):
    """Общий путь для одиночного поста и для собранного альбома.

    all_ids — все message_id, которые надо пометить seen (для альбома это вся
    группа: остальные кадры помечаются seen БЕЗ добавления в очередь).
    """
    chat_id = message.chat.id

    if db.is_seen("tg", chat_id, message.message_id):
        log.info("skip: seen | %s", ctx)
        return

    if not is_news(text):
        # Разделяем две очень разные причины: спам-словарь vs отсутствие маркера
        if not text.strip():
            why, bucket = "empty_text", "empty_text"
        else:
            hit = spam_hit(text)
            why, bucket = (("spam(%s)" % hit), "spam") if hit else ("no_marker", "no_marker")
        # Спам отброшен ОСОЗНАННО — помечаем seen, чтобы не перемалывать.
        # no_marker/empty_text НЕ помечаем: если маркеры окажутся настроены
        # неверно, эти посты не будут навсегда похоронены в seen.
        if bucket == "spam":
            db.mark_seen_many("tg", chat_id, all_ids)
        _stats[bucket] += 1
        log.info("skip: %s | %s", why, ctx)
        return

    reason = special_reason(text)
    kind = "special" if reason else "digest"
    # seen + очередь одной транзакцией — см. db.mark_seen_and_enqueue
    db.mark_seen_and_enqueue(
        "tg", chat_id, all_ids, message.message_id, text, kind, reason or "")
    _stats["queued"] += 1
    log.info("queued: %s%s | seen_ids=%s | %s",
             kind, (" (%s)" % reason) if reason else "", all_ids, ctx)


# ---------------------------------------------------- публикация

async def post_special():
    """Особые новости — отдельными постами. Если есть оригинал — копируем с фото."""
    rows = db.pending("special")
    posted = 0
    for qid, chat_id, msg_id, text, _reason, _created in rows:
        try:
            if chat_id and msg_id:
                # copy_message переносит фото + подпись без пометки «переслано»
                await bot.copy_message(config.TARGET_TG_CHANNEL, from_chat_id=chat_id, message_id=msg_id)
            else:
                await bot.send_message(config.TARGET_TG_CHANNEL, text.strip(), disable_web_page_preview=True)
        except Exception as e:
            log.warning("[post] copy_message не прошёл для qid=%s (%s), шлю текстом", qid, e)
            try:
                await bot.send_message(config.TARGET_TG_CHANNEL, text.strip(), disable_web_page_preview=True)
            except Exception:
                log.exception("[post] не смог опубликовать special qid=%s, оставляю в очереди", qid)
                continue
        db.mark_posted([qid])
        posted += 1
    log.info("[post] special=%d posted=%d", len(rows), posted)
    return posted


async def post_digest():
    """Дайджест: когда накопилось DIGEST_SIZE, либо старьё висит дольше FLUSH_AFTER_DAYS."""
    n = db.count_pending("digest")
    age = db.oldest_pending_age_days("digest")
    ready = bool(n) and (n >= config.DIGEST_SIZE or (age is not None and age >= config.FLUSH_AFTER_DAYS))

    if not ready:
        log.info("[post] digest_pending=%d oldest_age_days=%s ready=False posted=0", n, age)
        return 0

    rows = db.pending("digest", limit=config.DIGEST_SIZE)
    ids = [r[0] for r in rows]
    items = [r[3] for r in rows]

    sent = 0
    for message_text in build_digest_messages(items):
        await bot.send_message(config.TARGET_TG_CHANNEL, message_text, disable_web_page_preview=True)
        sent += 1

    db.mark_posted(ids)
    log.info("[post] digest_pending=%d oldest_age_days=%s ready=True posted=%d (сообщений=%d)",
             n, age, len(ids), sent)
    return len(ids)


async def preflight():
    """Один раз на старте: проверить, что бот видит оба чата и он там админ.

    Без этого «бот запущен, ошибок нет, постов нет» неотличимо от «бот вообще
    не админ в источнике» — или от «источник супергруппа, а мы слушаем канал».
    """
    me = await bot.get_me()
    log.info("preflight: бот @%s (id=%s) can_read_all_group_messages=%s",
             me.username, me.id, me.can_read_all_group_messages)
    log.info("preflight: allowed_updates=%s", dp.resolve_used_update_types())

    source_ref = config.SOURCE_TG_CHAT_ID or ("@" + config.SOURCE_TG_USERNAME)
    for label, ref, is_source in (("источник", source_ref, True),
                                  ("получатель", config.TARGET_TG_CHANNEL, False)):
        try:
            chat = await bot.get_chat(ref)
        except Exception as e:
            log.error("preflight: %s %r НЕДОСТУПЕН: %s. "
                      "Бот не добавлен в чат либо ссылка неверна — постов не будет.",
                      label, ref, e)
            continue

        log.info("preflight: %s ok — id=%s type=%s username=%r title=%r",
                 label, chat.id, chat.type, chat.username, chat.title)

        # Тип чата определяет ТИП АПДЕЙТА. Именно на этом бот молчал месяц.
        if is_source:
            if chat.type in ("group", "supergroup"):
                log.info("preflight: источник — %s, посты придут как 'message'", chat.type)
            elif chat.type == "channel":
                log.info("preflight: источник — канал, посты придут как 'channel_post'")
            else:
                log.warning("preflight: неожиданный тип источника %r", chat.type)

        try:
            member = await bot.get_chat_member(chat.id, me.id)
        except Exception as e:
            log.warning("preflight: не смог прочитать права в %s (%s): %s", label, ref, e)
            continue

        status = getattr(member, "status", "?")
        status = getattr(status, "value", status)
        if status not in ("administrator", "creator"):
            log.warning("preflight: бот НЕ АДМИН в %s (%r), статус=%r. "
                        "В источнике это значит, что посты оттуда не придут вовсе; "
                        "в получателе — что публикация упадёт.",
                        label, ref, status)
        else:
            log.info("preflight: права в %s — %s", label, status)


def check_filter_health():
    """Если что-то приходило, а в очередь за FLUSH_AFTER_DAYS дней не попало ничего —
    почти наверняка фильтр режет 100% постов (разъехались маркеры)."""
    seen = db.count_seen("tg") + db.count_seen("vk")
    rejected = _stats["spam"] + _stats["no_marker"] + _stats["empty_text"]
    queued = db.count_queue_since(config.FLUSH_AFTER_DAYS)
    if (seen or rejected) and not queued:
        log.warning(
            "фильтр отсёк 100%% постов, проверь маркеры: отклонено=%d "
            "(no_marker=%d spam=%d empty=%d), seen=%d, в очередь за %d дн. попало 0. "
            "Смотри строки 'skip: ...' выше",
            rejected, _stats["no_marker"], _stats["spam"], _stats["empty_text"],
            seen, config.FLUSH_AFTER_DAYS,
        )
    return seen, queued


# ---------------------------------------------------------------- команды
# ВАЖНО: команды регистрируются РАНЬШЕ общего message-хендлера ниже,
# иначе он перехватил бы /status и /testpost.

def _admin_only(message: Message) -> bool:
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS не задан — команда %r отклонена", (message.text or "")[:20])
        return False
    return message.from_user is not None and message.from_user.id in ADMIN_IDS


@dp.message(Command("status"), F.chat.type == "private")
async def cmd_status(message: Message):
    if not _admin_only(message):
        return

    db_path = db.DB
    try:
        size = os.path.getsize(db_path)
        size_s = "%.1f KB" % (size / 1024)
    except OSError as e:
        size_s = "НЕТ ФАЙЛА (%s)" % e

    nxt = "планировщик не подключён"
    if _scheduler is not None:
        jobs = _scheduler.get_jobs()
        nxt = ", ".join("%s -> %s (tz=%s)" % (j.id, j.next_run_time, j.trigger.timezone)
                        for j in jobs) or "джоб нет"

    lines = [
        "<b>Статус</b>",
        "digest в очереди: %d" % db.count_pending("digest"),
        "special в очереди: %d" % db.count_pending("special"),
        "возраст самой старой (digest): %s дн." % db.oldest_pending_age_days("digest"),
        "возраст самой старой (special): %s дн." % db.oldest_pending_age_days("special"),
        "seen tg / vk: %d / %d" % (db.count_seen("tg"), db.count_seen("vk")),
        "с момента старта: queued=%d no_marker=%d spam=%d empty=%d" % (
            _stats["queued"], _stats["no_marker"], _stats["spam"], _stats["empty_text"]),
        "",
        "БД: <code>%s</code>" % db_path,
        "размер БД: %s" % size_s,
        "",
        "следующий запуск: %s" % nxt,
        "источник: @%s (id=%s)" % (config.SOURCE_TG_USERNAME, config.SOURCE_TG_CHAT_ID),
        "получатель: %s" % config.TARGET_TG_CHANNEL,
        "allowed_updates: %s" % (dp.resolve_used_update_types(),),
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("testpost"), F.chat.type == "private")
async def cmd_testpost(message: Message):
    if not _admin_only(message):
        return
    await message.answer("Запускаю post_special() + post_digest()…")
    try:
        s = await post_special()
        d = await post_digest()
        await message.answer("Готово: special опубликовано=%s, digest опубликовано=%s" % (s, d))
    except Exception as e:
        log.exception("[testpost] ошибка")
        await message.answer("Ошибка: %s" % e)


# ------------------------------------------------- приём постов источника
# Регистрируются ПОСЛЕ команд — см. комментарий выше.

@dp.channel_post()
async def on_channel_post(message: Message):
    """Источник-канал (type=channel)."""
    await ingest(message, "channel_post")


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message(message: Message):
    """Источник-супергруппа (type=supergroup) — именно этот путь и был потерян."""
    await ingest(message, "message")
