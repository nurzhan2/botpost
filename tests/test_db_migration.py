# -*- coding: utf-8 -*-
"""J1: миграция seen на ключ (source, chat_id, msg_id). Должна быть идемпотентной."""
import importlib
import sqlite3

import pytest


OLD_SCHEMA = """
CREATE TABLE seen (
    source  TEXT,
    msg_id  INTEGER,
    PRIMARY KEY (source, msg_id)
);
CREATE TABLE queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT, chat_id INTEGER, msg_id INTEGER, text TEXT,
    kind TEXT, reason TEXT, created_at TEXT, posted INTEGER DEFAULT 0
);
"""


@pytest.fixture()
def old_db(tmp_path, monkeypatch):
    """База в СТАРОЙ схеме, ровно как в проде: одна строка ('vk', 0)."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO seen (source, msg_id) VALUES ('vk', 0)")
    con.commit()
    con.close()

    monkeypatch.setenv("DB_PATH", str(path))
    import db as db_module
    importlib.reload(db_module)
    return db_module, path


def _cols(path, table="seen"):
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
    con.close()
    return cols


def test_старая_схема_действительно_старая(old_db):
    _, path = old_db
    assert "chat_id" not in _cols(path)


def test_init_мигрирует_схему(old_db):
    db_module, path = old_db
    db_module.init()
    assert "chat_id" in _cols(path)


def test_данные_переносятся_а_не_теряются(old_db):
    db_module, path = old_db
    db_module.init()
    con = sqlite3.connect(path)
    rows = con.execute("SELECT source, chat_id, msg_id FROM seen").fetchall()
    con.close()
    assert rows == [("vk", 0, 0)], "прод-строка ('vk',0) должна доехать как ('vk',0,0)"


def test_миграция_идемпотентна(old_db):
    """Повторный запуск не должен ни падать, ни дублировать, ни терять."""
    db_module, path = old_db
    for _ in range(3):
        db_module.init()
    con = sqlite3.connect(path)
    rows = con.execute("SELECT source, chat_id, msg_id FROM seen").fetchall()
    con.close()
    assert rows == [("vk", 0, 0)]
    assert "chat_id" in _cols(path)


def test_новый_ключ_работает_после_миграции(old_db):
    db_module, path = old_db
    db_module.init()
    db_module.mark_seen("tg", -1003661440984, 150)
    db_module.mark_seen("tg", -1002117238801, 150)   # тот же msg_id, другой чат
    assert db_module.is_seen("tg", -1003661440984, 150)
    assert db_module.is_seen("tg", -1002117238801, 150)
    assert db_module.count_seen("tg") == 2


def test_чистая_база_сразу_в_новой_схеме(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fresh.db"))
    import db as db_module
    importlib.reload(db_module)
    db_module.init()
    assert "chat_id" in _cols(tmp_path / "fresh.db")
    assert db_module.count_seen("tg") == 0


def test_таблица_imported_создаётся_при_миграции(old_db):
    """У прод-базы её нет — import_history не должен упасть после деплоя."""
    db_module, path = old_db
    db_module.init()
    con = sqlite3.connect(path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "imported" in tables
