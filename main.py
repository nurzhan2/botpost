# -*- coding: utf-8 -*-
"""Точка входа: приём новых постов + ежедневная публикация по расписанию."""
import asyncio
import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import db
from bot import bot, dp, post_special, post_digest
from vk_source import listen_vk


async def post_job():
    try:
        await post_special()   # особые новости — отдельными постами
        await post_digest()    # общий дайджест по 10 штук
        logging.info("[post] done")
    except Exception as e:
        logging.exception("[post] error: %s", e)


async def main():
    logging.basicConfig(level=logging.INFO)
    db.init()

    sched = AsyncIOScheduler(timezone=config.TZ)
    hh, mm = config.POST_TIME.split(":")
    sched.add_job(post_job, CronTrigger(hour=int(hh), minute=int(mm)))
    sched.start()

    # VK читается в отдельном потоке (Long Poll блокирующий); если VK выключен — поток сразу выйдет
    threading.Thread(target=listen_vk, daemon=True).start()

    logging.info("Бот запущен. Слушаю источник, публикация в %s", config.POST_TIME)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
