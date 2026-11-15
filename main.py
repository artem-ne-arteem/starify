import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import load_config
from bot.db import Database
from bot.fragment import FragmentDelivery
from bot.handlers import routers
from bot.platega import Platega
from bot.service import Shop
from bot.workers import admin_timeout_watcher, payment_poller, recover_after_restart


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("starify")
    cfg = load_config()

    db = Database(cfg.db_path)
    await db.connect()
    platega = Platega(cfg.platega_merchant_id, cfg.platega_secret)
    fragment = FragmentDelivery(cfg)

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    shop = Shop(bot, cfg, db, platega, fragment)

    if not cfg.admin_ids:
        log.warning("ADMIN_IDS пуст — заказы будут уходить сразу в автодоставку")
    if not platega.configured:
        log.warning("Platega не настроена — оплата не будет работать")
    ok, why = fragment.available
    log.info("Автодоставка: %s", "включена" if ok else f"выключена ({why})")

    dp = Dispatcher(storage=MemoryStorage())
    dp["shop"] = shop
    dp.include_routers(*routers)

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await recover_after_restart(shop)
    tasks = [asyncio.create_task(payment_poller(shop)),
             asyncio.create_task(admin_timeout_watcher(shop))]
    me = await bot.get_me()
    log.info("Starify запущен: @%s", me.username)
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await platega.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
