# -*- coding: utf-8 -*-
"""Общая подготовка окружения для тестов."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# bot.py читает BOT_TOKEN на импорте — подставляем фиктивный,
# наружу тесты не ходят.
os.environ.setdefault("BOT_TOKEN", "0:test")

# Маркер-кружок в двух написаниях. Именно их расхождение и было причиной,
# по которой в канал не ушло ни одного поста.
REC_PLAIN = "⏺"            # ⏺   U+23FA
REC_EMOJI = "⏺️"      # ⏺️  U+23FA U+FE0F


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Чистая БД на каждый тест."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(path))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    db_module.init()
    return db_module
