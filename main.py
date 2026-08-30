# -*- coding: utf-8 -*-
"""Точка входа: приём новых постов + ежедневная публикация по расписанию."""
import asyncio
import logging
import threading
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import db
from bot import (bot, dp, post_special, post_digest, set_scheduler,
                 check_filter_health, preflight)
from vk_source import listen_vk, vk_enabled

log = logging.getLogger(__name__)


async def post_job():
    try:
        await post_special()   # особые новости — отдельными постами
        await post_digest()    # общий дайджест по 10 штук
        check_filter_health()  # WARNING, если фильтр режет вообще всё
    except Exception as e:
        logging.exception("[post] error: %s", e)


def build_post_trigger() -> CronTrigger:
    """Триггер ежедневной публикации.

    ВАЖНО: таймзону обязательно передавать в САМ CronTrigger.
    AsyncIOScheduler(timezone=...) применяется только когда триггер задан
    алиасом ('cron'); готовый инстанс CronTrigger берёт таймзону машины
    (на Railway это UTC) и джоба уезжает на 3 часа.

    Вынесено в отдельную функцию, чтобы тест мог проверить таймзону,
    не поднимая бота целиком.
    """
    tz = ZoneInfo(config.TZ)
    hh, mm = config.POST_TIME.split(":")
    return CronTrigger(hour=int(hh), minute=int(mm), timezone=tz)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init()

    sched = AsyncIOScheduler(timezone=ZoneInfo(config.TZ))
    job = sched.add_job(post_job, build_post_trigger(), id="daily_post")
    set_scheduler(sched)
    sched.start()

    log.info("scheduler tz=%s, job tz=%s, next run=%s (POST_TIME=%s)",
             sched.timezone, job.trigger.timezone, job.next_run_time, config.POST_TIME)

    # Проверяем доступ к каналам и права ДО того, как уйти в polling
    await preflight()

    # VK читается в отдельном потоке (Long Poll блокирующий).
    # Поток вообще не поднимаем, если VK не настроен — иначе он падал бы
    # по кругу и ежедневно засорял логи трейсбеками.
    if vk_enabled():
        threading.Thread(target=listen_vk, daemon=True).start()

    log.info("Бот запущен. Слушаю источник, публикация в %s", config.POST_TIME)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
