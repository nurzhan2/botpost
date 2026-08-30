# -*- coding: utf-8 -*-
"""Хендлер канала: альбомы (C) и момент простановки seen (G)."""
import asyncio

import pytest

from conftest import REC_PLAIN
import bot as bot_module


class FakeChat:
    def __init__(self, chat_id=-1001, username="semyadruj", title="Источник"):
        self.id = chat_id
        self.username = username
        self.title = title


class FakeMessage:
    def __init__(self, message_id, text=None, caption=None,
                 media_group_id=None, chat=None):
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.media_group_id = media_group_id
        self.chat = chat or FakeChat()


@pytest.fixture(autouse=True)
def _reset_stats():
    for k in bot_module._stats:
        bot_module._stats[k] = 0
    bot_module._album_buf.clear()
    bot_module.ALBUM_DEBOUNCE_SEC = 0.05
    yield


def send(message):
    asyncio.run(bot_module.on_channel_post(message))


async def _send_album(messages):
    for m in messages:
        await bot_module.on_channel_post(m)
    await asyncio.sleep(bot_module.ALBUM_DEBOUNCE_SEC * 4)


# ------------------------------------------------------------------- C

def test_альбом_с_подписью_у_второго_кадра_ставится_в_очередь_один_раз(fresh_db):
    """Регрессия: раньше кадры без подписи давали is_news False и альбом терялся."""
    msgs = [
        FakeMessage(10, caption=None, media_group_id="AG"),
        FakeMessage(11, caption=REC_PLAIN + " Крестный ход в Коломне", media_group_id="AG"),
        FakeMessage(12, caption=None, media_group_id="AG"),
    ]
    asyncio.run(_send_album(msgs))

    assert fresh_db.count_pending("digest") == 1
    assert bot_module._stats["queued"] == 1


def test_все_кадры_альбома_помечаются_seen(fresh_db):
    msgs = [
        FakeMessage(20, caption=None, media_group_id="BG"),
        FakeMessage(21, caption=REC_PLAIN + " Праздник в приходе", media_group_id="BG"),
        FakeMessage(22, caption=None, media_group_id="BG"),
    ]
    asyncio.run(_send_album(msgs))
    assert all(fresh_db.is_seen("tg", i) for i in (20, 21, 22))


def test_в_очередь_попадает_id_кадра_носителя(fresh_db):
    """copy_message должен взять именно тот кадр, где подпись."""
    msgs = [
        FakeMessage(30, caption=None, media_group_id="CG"),
        FakeMessage(31, caption=REC_PLAIN + " Лекция для родителей", media_group_id="CG"),
    ]
    asyncio.run(_send_album(msgs))
    rows = fresh_db.pending("digest")
    assert rows[0][2] == 31


def test_альбом_без_подписи_нигде_не_оседает(fresh_db):
    msgs = [FakeMessage(40, caption=None, media_group_id="DG"),
            FakeMessage(41, caption=None, media_group_id="DG")]
    asyncio.run(_send_album(msgs))
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["empty_text"] == 1


# ------------------------------------------------------------------- D

def test_чужой_канал_не_обрабатывается(fresh_db):
    send(FakeMessage(50, text=REC_PLAIN + " Новость",
                     chat=FakeChat(chat_id=-999, username="Friendl_family23")))
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["queued"] == 0


# ------------------------------------------------------------------- G

def test_новость_попадает_в_seen_и_в_очередь(fresh_db):
    send(FakeMessage(60, text=REC_PLAIN + " Настоящая новость"))
    assert fresh_db.is_seen("tg", 60)
    assert fresh_db.count_pending("digest") == 1


def test_спам_помечается_seen_но_не_ставится_в_очередь(fresh_db):
    send(FakeMessage(61, text="Вакансия: пономарь, зарплата от 40000"))
    assert fresh_db.is_seen("tg", 61)
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["spam"] == 1


def test_пост_без_маркера_НЕ_помечается_seen(fresh_db):
    """Ключевая семантика G: не хоронить в seen то, что могло быть отсеяно
    из-за неверно настроенных маркеров."""
    send(FakeMessage(62, text="Поздравление без маркера"))
    assert not fresh_db.is_seen("tg", 62)
    assert bot_module._stats["no_marker"] == 1


def test_повторная_обработка_того_же_поста_не_даёт_дубля(fresh_db):
    msg = FakeMessage(63, text=REC_PLAIN + " Новость")
    send(msg)
    send(msg)                     # как при переотдаче апдейта Telegram'ом
    assert fresh_db.count_pending("digest") == 1


def test_повторная_обработка_отсеянного_идемпотентна(fresh_db):
    """no_marker не в seen, но повторный прогон снова отсеивает — вреда нет."""
    msg = FakeMessage(64, text="Без маркера")
    send(msg)
    send(msg)
    assert fresh_db.count_pending("digest") == 0


def test_special_уходит_в_свою_очередь(fresh_db):
    send(FakeMessage(65, text=REC_PLAIN + " Насибулин возглавил встречу"))
    assert fresh_db.count_pending("special") == 1
    assert fresh_db.count_pending("digest") == 0


# ------------------------------------------------------- health-проверка

def test_warning_когда_фильтр_отсёк_всё(fresh_db, caplog):
    send(FakeMessage(70, text="Поздравление без маркера"))
    send(FakeMessage(71, text="Ещё одно поздравление"))
    with caplog.at_level("WARNING"):
        bot_module.check_filter_health()
    assert "фильтр отсёк 100% постов" in caplog.text


def test_нет_warning_когда_очередь_наполняется(fresh_db, caplog):
    send(FakeMessage(72, text=REC_PLAIN + " Настоящая новость"))
    with caplog.at_level("WARNING"):
        bot_module.check_filter_health()
    assert "отсёк 100%" not in caplog.text
