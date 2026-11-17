import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from app.access import AccessMiddleware
from app.cloner import ops
from app.config import Config, load_config
from app.db import Db
from app.factory import make_bot
from app.handlers import admin, create, menu, packs, panel, start, workers


def build_dispatcher(db: Db, cfg: Config) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["config"] = cfg
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.include_routers(admin.router, panel.router, start.router, menu.router, packs.router, workers.router,
                       create.router)
    return dp


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = Db(cfg.db_path)
    await db.open()
    await ops.sync_env_session(db, cfg)

    bot = make_bot(cfg.bot_token, cfg.proxy, html=True)
    dp = build_dispatcher(db, cfg)
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
