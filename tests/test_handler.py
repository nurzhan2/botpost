# -*- coding: utf-8 -*-
"""Приём постов: супергруппа (J), канал, альбомы (C/J3), момент seen (G)."""
import asyncio

import pytest
from aiogram import F

from conftest import REC_PLAIN
import bot as bot_module
import config

SOURCE_ID = config.SOURCE_TG_CHAT_ID or -1003661440984


class FakeChat:
    def __init__(self, chat_id=SOURCE_ID, username="semyadruj",
                 chat_type="supergroup", title="Источник"):
        self.id = chat_id
        self.username = username
        self.type = chat_type
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
def _reset_state():
    for k in bot_module._stats:
        bot_module._stats[k] = 0
    bot_module._album_buf.clear()
    bot_module.ALBUM_DEBOUNCE_SEC = 0.05
    yield


def send(message, origin="message"):
    asyncio.run(bot_module.ingest(message, origin))


async def _send_album(messages, origin="message"):
    for m in messages:
        await bot_module.ingest(m, origin)
    await asyncio.sleep(bot_module.ALBUM_DEBOUNCE_SEC * 4)


# ------------------------------------------------------- J: супергруппа

def test_пост_из_супергруппы_попадает_в_очередь(fresh_db):
    """Главная регрессия: источник — supergroup, апдейт приходит как 'message'."""
    send(FakeMessage(100, text=REC_PLAIN + " Крестный ход в Коломне"))
    assert fresh_db.count_pending("digest") == 1
    assert bot_module._stats["queued"] == 1


def test_супергруппа_матчится_по_числовому_id_без_username(fresh_db):
    """Приватная супергруппа username не имеет — должен спасать SOURCE_TG_CHAT_ID."""
    chat = FakeChat(chat_id=SOURCE_ID, username=None)
    send(FakeMessage(101, text=REC_PLAIN + " Новость без username", chat=chat))
    assert fresh_db.count_pending("digest") == 1


def test_канал_источник_тоже_работает(fresh_db):
    """channel_post оставлен на случай, если источник станет каналом."""
    chat = FakeChat(chat_type="channel")
    send(FakeMessage(102, text=REC_PLAIN + " Новость из канала", chat=chat), origin="channel_post")
    assert fresh_db.count_pending("digest") == 1


def test_чужой_чат_не_обрабатывается(fresh_db):
    """Эхо из канала-получателя должно отбраковываться."""
    chat = FakeChat(chat_id=-1002117238801, username="Friendl_family23", chat_type="channel")
    send(FakeMessage(103, text=REC_PLAIN + " Эхо", chat=chat), origin="channel_post")
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["queued"] == 0


# ------------------------------------------------ J2: порядок хендлеров

def test_команды_зарегистрированы_раньше_общего_хендлера():
    """Иначе message-хендлер супергруппы перехватил бы /status и /testpost."""
    names = [h.callback.__name__ for h in bot_module.dp.message.handlers]
    assert "cmd_status" in names and "on_group_message" in names
    assert names.index("cmd_status") < names.index("on_group_message")
    assert names.index("cmd_testpost") < names.index("on_group_message")


def test_фильтр_общего_хендлера_не_пускает_личку():
    """/status приходит из private — конвейер новостей его не должен видеть."""
    flt = F.chat.type.in_({"group", "supergroup"})
    assert flt.resolve(FakeMessage(1, chat=FakeChat(chat_type="private"))) is False
    assert flt.resolve(FakeMessage(1, chat=FakeChat(chat_type="supergroup"))) is True
    assert flt.resolve(FakeMessage(1, chat=FakeChat(chat_type="group"))) is True


def test_личное_сообщение_не_попадает_в_конвейер(fresh_db):
    """Даже если бы дошло до ingest — не наш чат, в очередь не ляжет."""
    chat = FakeChat(chat_id=12345, username=None, chat_type="private")
    send(FakeMessage(104, text="/status", chat=chat))
    assert fresh_db.count_pending("digest") == 0


def test_allowed_updates_включает_оба_типа():
    types = bot_module.dp.resolve_used_update_types()
    assert "message" in types, "без 'message' посты супергруппы Telegram не отдаст"
    assert "channel_post" in types


# ------------------------------------------- J3: альбомы на обоих путях

@pytest.mark.parametrize("origin,chat_type", [("message", "supergroup"),
                                              ("channel_post", "channel")])
def test_альбом_с_подписью_у_второго_кадра(fresh_db, origin, chat_type):
    """Общий буфер обязан работать одинаково на обоих путях."""
    chat = FakeChat(chat_type=chat_type)
    msgs = [
        FakeMessage(200, caption=None, media_group_id="AG", chat=chat),
        FakeMessage(201, caption=REC_PLAIN + " Крестный ход", media_group_id="AG", chat=chat),
        FakeMessage(202, caption=None, media_group_id="AG", chat=chat),
    ]
    asyncio.run(_send_album(msgs, origin))
    assert fresh_db.count_pending("digest") == 1
    assert all(fresh_db.is_seen("tg", chat.id, i) for i in (200, 201, 202))
    # в очередь идёт id кадра-носителя — его copy_message перенесёт с фото
    assert fresh_db.pending("digest")[0][2] == 201


def test_альбомы_из_разных_чатов_не_смешиваются(fresh_db):
    """Ключ буфера включает chat_id: одинаковый media_group_id из двух чатов."""
    src = FakeChat()
    other = FakeChat(chat_id=-1002117238801, username="Friendl_family23", chat_type="channel")

    async def run():
        await bot_module.ingest(FakeMessage(300, caption=REC_PLAIN + " Из источника",
                                            media_group_id="SAME", chat=src), "message")
        await bot_module.ingest(FakeMessage(300, caption=REC_PLAIN + " Из чужого",
                                            media_group_id="SAME", chat=other), "channel_post")
        await asyncio.sleep(bot_module.ALBUM_DEBOUNCE_SEC * 4)
    asyncio.run(run())

    # чужой чат отсеян ещё до буфера, наш — доехал
    assert fresh_db.count_pending("digest") == 1


def test_альбом_без_подписи_нигде_не_оседает(fresh_db):
    msgs = [FakeMessage(400, caption=None, media_group_id="DG"),
            FakeMessage(401, caption=None, media_group_id="DG")]
    asyncio.run(_send_album(msgs))
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["empty_text"] == 1


# ---------------------------------------------------- J1: chat_id в seen

def test_одинаковый_msg_id_из_разных_чатов_не_блокирует(fresh_db):
    """Регрессия J1: раньше ключ был (source, msg_id) и пост №150 из одного
    чата навсегда блокировал пост №150 из другого."""
    fresh_db.mark_seen("tg", -1002117238801, 150)
    assert fresh_db.is_seen("tg", -1002117238801, 150)
    assert not fresh_db.is_seen("tg", SOURCE_ID, 150)

    send(FakeMessage(150, text=REC_PLAIN + " Пост номер 150 из источника"))
    assert fresh_db.count_pending("digest") == 1


# --------------------------------------------------------------- G

def test_новость_попадает_в_seen_и_в_очередь(fresh_db):
    send(FakeMessage(500, text=REC_PLAIN + " Настоящая новость"))
    assert fresh_db.is_seen("tg", SOURCE_ID, 500)
    assert fresh_db.count_pending("digest") == 1


def test_спам_помечается_seen_но_не_ставится_в_очередь(fresh_db):
    send(FakeMessage(501, text="Вакансия: пономарь, зарплата от 40000"))
    assert fresh_db.is_seen("tg", SOURCE_ID, 501)
    assert fresh_db.count_pending("digest") == 0
    assert bot_module._stats["spam"] == 1


def test_пост_без_маркера_НЕ_помечается_seen(fresh_db):
    send(FakeMessage(502, text="Поздравление без маркера"))
    assert not fresh_db.is_seen("tg", SOURCE_ID, 502)
    assert bot_module._stats["no_marker"] == 1


def test_повторная_обработка_не_даёт_дубля(fresh_db):
    msg = FakeMessage(503, text=REC_PLAIN + " Новость")
    send(msg)
    send(msg)
    assert fresh_db.count_pending("digest") == 1


def test_special_уходит_в_свою_очередь(fresh_db):
    send(FakeMessage(504, text=REC_PLAIN + " Насибулин возглавил встречу"))
    assert fresh_db.count_pending("special") == 1
    assert fresh_db.count_pending("digest") == 0


# ------------------------------------------------------- health-проверка

def test_warning_когда_фильтр_отсёк_всё(fresh_db, caplog):
    send(FakeMessage(600, text="Поздравление без маркера"))
    send(FakeMessage(601, text="Ещё одно поздравление"))
    with caplog.at_level("WARNING"):
        bot_module.check_filter_health()
    assert "фильтр отсёк 100% постов" in caplog.text


def test_нет_warning_когда_очередь_наполняется(fresh_db, caplog):
    send(FakeMessage(602, text=REC_PLAIN + " Настоящая новость"))
    with caplog.at_level("WARNING"):
        bot_module.check_filter_health()
    assert "отсёк 100%" not in caplog.text
