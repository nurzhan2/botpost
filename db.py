# -*- coding: utf-8 -*-
"""Хранилище: что уже видели + очередь на публикацию (SQLite)."""
import sqlite3
import datetime
import os

# Путь к базе. На Railway задать DB_PATH=/data/bot.db и примонтировать volume в /data,
# иначе при передеплое память бота стирается и старые новости публикуются повторно.
DB = os.environ.get("DB_PATH", "bot.db")


def _con():
    return sqlite3.connect(DB)


def init():
    con = _con()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS seen (
        source  TEXT,
        msg_id  INTEGER,
        PRIMARY KEY (source, msg_id)
    );
    CREATE TABLE IF NOT EXISTS queue (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT,
        chat_id    INTEGER,          -- id канала-источника (для copy с фото), NULL для импорта
        msg_id     INTEGER,          -- id сообщения в источнике, NULL для импорта
        text       TEXT,
        kind       TEXT,             -- 'digest' | 'special'
        reason     TEXT,
        created_at TEXT,
        posted     INTEGER DEFAULT 0
    );
    """)
    con.commit()
    con.close()


def max_seen_id(source):
    con = _con()
    row = con.execute("SELECT MAX(msg_id) FROM seen WHERE source=?", (source,)).fetchone()
    con.close()
    return row[0] or 0

def count_seen(source) -> int:
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM seen WHERE source=?", (source,)).fetchone()[0]
    con.close()
    return n


def is_seen(source, msg_id) -> bool:
    con = _con()
    row = con.execute("SELECT 1 FROM seen WHERE source=? AND msg_id=?", (source, msg_id)).fetchone()
    con.close()
    return row is not None


def mark_seen(source, msg_id):
    con = _con()
    con.execute("INSERT OR IGNORE INTO seen (source, msg_id) VALUES (?,?)", (source, msg_id))
    con.commit()
    con.close()


def mark_seen_many(source, msg_ids):
    """Пометить seen пачкой — нужно для альбомов: вся медиагруппа за раз."""
    if not msg_ids:
        return
    con = _con()
    con.executemany("INSERT OR IGNORE INTO seen (source, msg_id) VALUES (?,?)",
                    [(source, i) for i in msg_ids])
    con.commit()
    con.close()


def mark_seen_and_enqueue(source, msg_ids, chat_id, msg_id, text, kind, reason=""):
    """Пометить seen и поставить в очередь ОДНОЙ транзакцией.

    Порознь их делать нельзя: если процесс умрёт между add_to_queue и
    mark_seen, Telegram переотдаст неподтверждённый апдейт (offset не сдвинулся)
    и новость встанет в очередь второй раз -> дубль в дайджесте.
    """
    con = _con()
    try:
        with con:   # commit при успехе, rollback при исключении
            if msg_ids:
                con.executemany("INSERT OR IGNORE INTO seen (source, msg_id) VALUES (?,?)",
                                [(source, i) for i in msg_ids])
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


def mark_posted(ids):
    con = _con()
    con.executemany("UPDATE queue SET posted=1 WHERE id=?", [(i,) for i in ids])
    con.commit()
    con.close()
