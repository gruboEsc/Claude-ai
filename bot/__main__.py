import asyncio
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import reminders
from .claude import Claude
from .config import Config
from .db import Storage
from .handlers import setup

COMMANDS = [
    BotCommand(command="new", description="🧹 Новый диалог"),
    BotCommand(command="mode", description="🎭 Режим: программист, переводчик…"),
    BotCommand(command="effort", description="🧠 Глубина размышлений"),
    BotCommand(command="reminders", description="⏰ Мои напоминания"),
    BotCommand(command="export", description="📥 Скачать диалог"),
    BotCommand(command="stats", description="📊 Статистика и расходы"),
    BotCommand(command="help", description="❓ Справка"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if not cfg.allowed_users:
        logging.warning("ALLOWED_USERS пуст — бот отвечает всем, и платишь за это ты")

    storage = Storage(cfg.db_path)
    await storage.open()
    remind = reminders.Reminders(storage, ZoneInfo(cfg.timezone))

    bot = Bot(cfg.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(setup(cfg, storage, Claude(cfg, reminders.TOOLS), remind))
    await bot.set_my_commands(COMMANDS)
    delivery = asyncio.create_task(remind.deliver_forever(bot))

    try:
        await dp.start_polling(bot)
    finally:
        delivery.cancel()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
