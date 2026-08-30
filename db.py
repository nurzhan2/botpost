# -*- coding: utf-8 -*-
"""Хранилище: что уже видели + очередь на публикацию (SQLite)."""
import sqlite3
import datetime
import logging
import os

log = logging.getLogger(__name__)

# Путь к базе. На Railway задать DB_PATH=/data/bot.db и примонтировать volume в /data,
# иначе при передеплое память бота стирается и старые новости публикуются повторно.
DB = os.environ.get("DB_PATH", "bot.db")


def _con():
    return sqlite3.connect(DB)


def _seen_columns(con):
    return [r[1] for r in con.execute("PRAGMA table_info(seen)")]


def _migrate_seen_add_chat_id(con):
    """Добавить chat_id в ключ seen. Идемпотентно: повторный запуск ничего не делает.

    Зачем: msg_id уникален только ВНУТРИ чата. С ключом (source, msg_id) пост
    №150 из источника заблокировал бы пост №150 из любого другого чата.
    Пока tg-веток не было вообще, это не стреляло; теперь апдейты приходят
    из двух чатов сразу (супергруппа-источник и эхо канала-получателя).
    """
    cols = _seen_columns(con)
    if not cols or "chat_id" in cols:
        return False        # таблицы ещё нет (создастся сразу новой) или уже мигрировано

    log.warning("db: миграция seen -> ключ (source, chat_id, msg_id)")
    with con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS seen_new (
                source  TEXT,
                chat_id INTEGER,
                msg_id  INTEGER,
                PRIMARY KEY (source, chat_id, msg_id)
            )
        """)
        # Старым записям chat_id неизвестен -> 0. Их единицы, и они от VK.
        con.execute("INSERT OR IGNORE INTO seen_new (source, chat_id, msg_id) "
                    "SELECT source, 0, msg_id FROM seen")
        moved = con.execute("SELECT COUNT(*) FROM seen_new").fetchone()[0]
        con.execute("DROP TABLE seen")
        con.execute("ALTER TABLE seen_new RENAME TO seen")
    log.warning("db: миграция seen завершена, перенесено записей: %d", moved)
    return True


def init():
    con = _con()
    # Сначала миграция старой схемы, если база досталась от прежней версии.
    _migrate_seen_add_chat_id(con)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS seen (
        source  TEXT,
        chat_id INTEGER,
        msg_id  INTEGER,
        PRIMARY KEY (source, chat_id, msg_id)
    );
    CREATE TABLE IF NOT EXISTS queue (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT,
        chat_id    INTEGER,          -- id чата-источника (для copy с фото), NULL для импорта
        msg_id     INTEGER,          -- id сообщения в источнике, NULL для импорта
        text       TEXT,
        kind       TEXT,             -- 'digest' | 'special'
        reason     TEXT,
        created_at TEXT,
        posted     INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS imported (
        text_hash  TEXT PRIMARY KEY,   -- sha256 нормализованного текста
        created_at TEXT
    );
    """)
    con.commit()
    con.close()


def count_seen(source) -> int:
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM seen WHERE source=?", (source,)).fetchone()[0]
    con.close()
    return n


def is_seen(source, chat_id, msg_id) -> bool:
    con = _con()
    row = con.execute("SELECT 1 FROM seen WHERE source=? AND chat_id=? AND msg_id=?",
                      (source, chat_id, msg_id)).fetchone()
    con.close()
    return row is not None


def mark_seen(source, chat_id, msg_id):
    con = _con()
    con.execute("INSERT OR IGNORE INTO seen (source, chat_id, msg_id) VALUES (?,?,?)",
                (source, chat_id, msg_id))
    con.commit()
    con.close()


def mark_seen_many(source, chat_id, msg_ids):
    """Пометить seen пачкой — нужно для альбомов: вся медиагруппа за раз."""
    if not msg_ids:
        return
    con = _con()
    con.executemany("INSERT OR IGNORE INTO seen (source, chat_id, msg_id) VALUES (?,?,?)",
                    [(source, chat_id, i) for i in msg_ids])
    con.commit()
    con.close()


def mark_seen_and_enqueue(source, chat_id, msg_ids, msg_id, text, kind, reason=""):
    """Пометить seen и поставить в очередь ОДНОЙ транзакцией.

    Порознь их делать нельзя: если процесс умрёт между add_to_queue и
    mark_seen, Telegram переотдаст неподтверждённый апдейт (offset не сдвинулся)
    и новость встанет в очередь второй раз -> дубль в дайджесте.
    """
    con = _con()
    try:
        with con:   # commit при успехе, rollback при исключении
            if msg_ids:
                con.executemany(
                    "INSERT OR IGNORE INTO seen (source, chat_id, msg_id) VALUES (?,?,?)",
                    [(source, chat_id, i) for i in msg_ids])
            con.execute(
                "INSERT INTO queue (source, chat_id, msg_id, text, kind, reason, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (source, chat_id, msg_id, text, kind, reason,
                 datetime.datetime.utcnow().isoformat()),
            )
    finally:
        con.close()


def add_to_queue(source, chat_id, msg_id, text, kind, reason=""):
    con = _con()
    con.execute(
        "INSERT INTO queue (source, chat_id, msg_id, text, kind, reason, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (source, chat_id, msg_id, text, kind, reason, datetime.datetime.utcnow().isoformat()),
    )
    con.commit()
    con.close()


def pending(kind, limit=None):
    """-> [(id, chat_id, msg_id, text, reason, created_at), ...]"""
    con = _con()
    q = ("SELECT id, chat_id, msg_id, text, reason, created_at "
         "FROM queue WHERE posted=0 AND kind=? ORDER BY id")
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = con.execute(q, (kind,)).fetchall()
    con.close()
    return rows


def count_pending(kind) -> int:
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM queue WHERE posted=0 AND kind=?", (kind,)).fetchone()[0]
    con.close()
    return n


def oldest_pending_age_days(kind):
    con = _con()
    row = con.execute("SELECT MIN(created_at) FROM queue WHERE posted=0 AND kind=?", (kind,)).fetchone()
    con.close()
    if not row or not row[0]:
        return None
    created = datetime.datetime.fromisoformat(row[0])
    return (datetime.datetime.utcnow() - created).days


def count_queue_since(days: int) -> int:
    """Сколько записей вообще попало в очередь за последние N дней (включая опубликованные)."""
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM queue WHERE created_at >= ?", (since,)).fetchone()[0]
    con.close()
    return n


def import_enqueue(text_hash, text, kind, reason="") -> bool:
    """Импорт истории: поставить в очередь, если такого текста ещё не было.

    Идемпотентность по хешу текста — повторный прогон того же экспорта ничего
    не задваивает. chat_id/msg_id остаются NULL: у импортированных постов нет
    живого оригинала в канале, копировать нечего, публикуем текстом.
    Возвращает True, если запись добавлена.
    """
    con = _con()
    try:
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO imported (text_hash, created_at) VALUES (?,?)",
                (text_hash, datetime.datetime.utcnow().isoformat()),
            )
            if cur.rowcount == 0:
                return False          # уже импортировали раньше
            con.execute(
                "INSERT INTO queue (source, chat_id, msg_id, text, kind, reason, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("import", None, None, text, kind, reason,
                 datetime.datetime.utcnow().isoformat()),
            )
            return True
    finally:
        con.close()


def queue_has_text(text) -> bool:
    """Не лежит ли такой же текст уже в очереди (пересечение импорта с живой лентой)."""
    con = _con()
    row = con.execute("SELECT 1 FROM queue WHERE text=? LIMIT 1", (text,)).fetchone()
    con.close()
    return row is not None


def mark_posted(ids):
    con = _con()
    con.executemany("UPDATE queue SET posted=1 WHERE id=?", [(i,) for i in ids])
    con.commit()
    con.close()
