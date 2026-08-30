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
from bot import bot, dp, post_special, post_digest, set_scheduler, check_filter_health
from vk_source import listen_vk

log = logging.getLogger(__name__)


async def post_job():
    try:
        await post_special()   # особые новости — отдельными постами
        await post_digest()    # общий дайджест по 10 штук
        check_filter_health()  # WARNING, если фильтр режет вообще всё
    except Exception as e:
        logging.exception("[post] error: %s", e)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init()

    # ВАЖНО: таймзону обязательно передавать в САМ CronTrigger.
    # AsyncIOScheduler(timezone=...) применяется только когда триггер задан
    # алиасом ('cron'); готовый инстанс CronTrigger берёт таймзону машины
    # (на Railway это UTC) и джоба уезжает на 3 часа.
    tz = ZoneInfo(config.TZ)
    sched = AsyncIOScheduler(timezone=tz)
    hh, mm = config.POST_TIME.split(":")
    job = sched.add_job(
        post_job,
        CronTrigger(hour=int(hh), minute=int(mm), timezone=tz),
        id="daily_post",
    )
    set_scheduler(sched)
    sched.start()

    log.info("scheduler tz=%s, job tz=%s, next run=%s (POST_TIME=%s)",
             sched.timezone, job.trigger.timezone, job.next_run_time, config.POST_TIME)

    # VK читается в отдельном потоке (Long Poll блокирующий); если VK выключен — поток сразу выйдет
    threading.Thread(target=listen_vk, daemon=True).start()

    log.info("Бот запущен. Слушаю источник, публикация в %s", config.POST_TIME)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
