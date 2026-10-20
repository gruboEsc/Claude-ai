import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiohttp import web

from .billing import Billing
from .brain import Companion
from .channels import TelegramChannel
from .claude import Claude
from .companion import COMPACTION_INSTRUCTIONS
from .config import Config
from .db import Storage
from .handlers import setup
from .line import LineBot, LineChannel, web_app
from .tools import TOOLS, Tools
from .yookassa import YooKassa

COMMANDS = [
    BotCommand(command="status", description="⭐ Подписка"),
    BotCommand(command="settings", description="⚙️ Писать ли мне первым"),
    BotCommand(command="language", description="🌍 Язык / Language"),
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
    bot = Bot(cfg.telegram_token)
    channels = [TelegramChannel(bot)]
    line_channel = LineChannel(cfg.line_secret, cfg.line_token) if cfg.line_secret and cfg.line_token else None
    if line_channel:
        channels.append(line_channel)
    companion = Companion(cfg, storage, claude, Tools(storage, cfg.timezone), channels)

    yk = YooKassa(cfg.yk_shop_id, cfg.yk_secret) if cfg.yk_shop_id and cfg.yk_secret else None
    if not yk:
        logging.info("ЮKassa не настроена — оплата только звёздами")
    billing = Billing(cfg, storage, yk, notify=companion.notify)

    dp = Dispatcher()
    dp.include_router(setup(cfg, companion, billing))
    await bot.set_my_commands(COMMANDS)
    loops = [asyncio.create_task(companion.run_forever())]
    if billing.enabled:
        loops.append(asyncio.create_task(billing.run_forever()))

    runner = None
    if line_channel:
        runner = web.AppRunner(web_app(LineBot(cfg, companion, line_channel)))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.web_port).start()
        logging.info("LINE: вебхук слушает порт %s (/line/webhook)", cfg.web_port)

    try:
        await dp.start_polling(bot)
    finally:
        for task in loops:
            task.cancel()
        if runner:
            await runner.cleanup()
        if line_channel:
            await line_channel.close()
        if yk:
            await yk.close()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
