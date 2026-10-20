import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from .brain import Companion
from .claude import Claude
from .companion import COMPACTION_INSTRUCTIONS
from .config import Config
from .db import Storage
from .handlers import setup
from .tools import TOOLS, Tools

COMMANDS = [
    BotCommand(command="status", description="⭐ Подписка"),
    BotCommand(command="settings", description="⚙️ Писать ли мне первым"),
    BotCommand(command="forget", description="🧹 Стереть всё"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="terms", description="📄 Условия"),
    BotCommand(command="paysupport", description="💳 Помощь с оплатой"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if not cfg.admin_ids:
        logging.warning("ADMIN_IDS пуст — команда /admin никому не доступна")

    storage = Storage(cfg.db_path)
    await storage.open()
    claude = Claude(cfg, TOOLS, COMPACTION_INSTRUCTIONS)
    companion = Companion(cfg, storage, claude, Tools(storage, cfg.timezone))

    bot = Bot(cfg.telegram_token)
    dp = Dispatcher()
    dp.include_router(setup(cfg, companion))
    await bot.set_my_commands(COMMANDS)
    scheduler = asyncio.create_task(companion.run_forever(bot))

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
