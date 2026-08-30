# -*- coding: utf-8 -*-
"""
Расписание должно считаться в Europe/Moscow независимо от таймзоны машины.

Исходный баг: main.py передавал в add_job готовый инстанс CronTrigger, а
AsyncIOScheduler(timezone=...) применяет свою таймзону только к триггерам,
заданным алиасом ('cron'). Инстанс подхватывал таймзону хоста — на Railway
это UTC, и публикация уезжала на 10:00 UTC = 13:00 МСК.
"""
import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

import config
import main


@pytest.fixture()
def чужая_таймзона_машины(monkeypatch):
    """Притворяемся, что машина живёт в Нью-Йорке (как Railway живёт в UTC)."""
    import tzlocal
    other = ZoneInfo("America/New_York")
    monkeypatch.setattr(tzlocal, "get_localzone", lambda: other)
    try:                                   # apscheduler импортирует символ к себе
        import apscheduler.util as apu
        monkeypatch.setattr(apu, "get_localzone", lambda: other, raising=False)
    except ImportError:
        pass
    return other


def test_триггер_в_москве_при_чужой_таймзоне_машины(чужая_таймзона_машины):
    trigger = main.build_post_trigger()
    assert str(trigger.timezone) == "Europe/Moscow"


def test_следующий_запуск_ровно_в_post_time_по_москве(чужая_таймзона_машины):
    trigger = main.build_post_trigger()
    tz = ZoneInfo(config.TZ)
    now = datetime.datetime(2026, 8, 31, 6, 0, tzinfo=tz)
    nxt = trigger.get_next_fire_time(None, now)

    assert nxt.strftime("%H:%M") == config.POST_TIME
    assert nxt.utcoffset() == datetime.timedelta(hours=3)   # МСК = UTC+3


def test_голый_CronTrigger_подхватывает_таймзону_машины(чужая_таймзона_машины):
    """Документирует сам баг: так писать НЕЛЬЗЯ.

    Если этот тест однажды упадёт — значит apscheduler изменил поведение
    и комментарий в build_post_trigger можно пересмотреть.
    """
    naive = CronTrigger(hour=10, minute=0)
    assert str(naive.timezone) != "Europe/Moscow"


def test_zoneinfo_база_доступна():
    """На Railway zoneinfo читает системную базу — для неё в requirements есть tzdata."""
    assert ZoneInfo(config.TZ) is not None
