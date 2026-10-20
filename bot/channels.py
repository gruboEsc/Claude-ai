"""Messengers the companion talks through. The core only needs to send and to show typing."""

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

log = logging.getLogger(__name__)


class Channel:
    """One messenger. `external_id` is the person's id on that messenger."""

    name: str

    async def send(self, external_id: str, parts: list[str]) -> bool:
        """Deliver the messages in order; False if the person has blocked the bot."""
        raise NotImplementedError

    async def typing(self, external_id: str) -> None:
        """Show that the companion is typing, for a few seconds."""


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, bot: Bot):
        self.bot = bot

    async def send(self, external_id: str, parts: list[str]) -> bool:
        chat_id = int(external_id)
        try:
            for i, part in enumerate(parts):
                if i:
                    # Type each follow-up for a moment, like a person sending several messages.
                    await self.bot.send_chat_action(chat_id, ChatAction.TYPING)
                    await asyncio.sleep(min(1.0 + len(part) / 40, 4.0))
                await self.bot.send_message(chat_id, part, parse_mode=None)
        except TelegramForbiddenError:
            return False
        return True

    async def typing(self, external_id: str) -> None:
        try:
            await self.bot.send_chat_action(int(external_id), ChatAction.TYPING)
        except TelegramAPIError:
            log.debug("typing indicator failed", exc_info=True)
