"""Reminders that Claude sets through tool calls ("напомни завтра в 9 позвонить маме")."""

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .db import Storage

log = logging.getLogger(__name__)

POLL_INTERVAL = 15  # seconds

TOOLS = [
    {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder that the bot will send to this chat at the given moment. "
            "Use it whenever the user asks to remind them of something. Resolve relative times "
            "('через 2 часа', 'завтра утром') against the current time given in the user's message."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "Local date and time in ISO 8601, e.g. 2026-10-21T09:00",
                },
                "text": {"type": "string", "description": "What to remind about, phrased for the user"},
            },
            "required": ["when", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_reminders",
        "description": "List this chat's pending reminders with their ids.",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by id (get ids from list_reminders).",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]


class Reminders:
    def __init__(self, storage: Storage, tz):
        self.storage = storage
        self.tz = tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def run_tool(self, chat_id: int, name: str, args: dict) -> str:
        if name == "set_reminder":
            when, text = args.get("when"), args.get("text")
            if not isinstance(when, str) or not isinstance(text, str) or not text.strip():
                raise ValueError("set_reminder needs string fields 'when' and 'text'.")
            try:
                due = datetime.fromisoformat(when)
            except ValueError:
                raise ValueError(f"'when' is not ISO 8601: {when!r}") from None
            if due.tzinfo is None:
                due = due.replace(tzinfo=self.tz)
            if due <= self.now():
                raise ValueError("That moment is already in the past.")
            rid = await self.storage.add_reminder(chat_id, int(due.timestamp()), text.strip())
            return f"Reminder #{rid} set for {due.astimezone(self.tz):%Y-%m-%d %H:%M}."

        if name == "list_reminders":
            rows = await self.storage.pending_reminders(chat_id)
            if not rows:
                return "No pending reminders."
            return "\n".join(f"#{rid} at {self._fmt(due)}: {text}" for rid, due, text in rows)

        if name == "cancel_reminder":
            rid = args.get("id")
            if not isinstance(rid, int):
                raise ValueError("cancel_reminder needs an integer 'id'.")
            ok = await self.storage.cancel_reminder(chat_id, rid)
            return f"Reminder #{rid} cancelled." if ok else f"No pending reminder #{rid} in this chat."

        raise ValueError(f"Unknown tool {name!r}.")

    def _fmt(self, ts: int) -> str:
        return f"{datetime.fromtimestamp(ts, timezone.utc).astimezone(self.tz):%Y-%m-%d %H:%M}"

    async def deliver_forever(self, bot: Bot) -> None:
        while True:
            try:
                for rid, chat_id, text in await self.storage.due_reminders(int(self.now().timestamp())):
                    try:
                        await bot.send_message(chat_id, f"⏰ <b>Напоминание</b>\n{_escape(text)}")
                    except TelegramAPIError as e:
                        log.warning("reminder %s to %s failed: %s", rid, chat_id, e)
                    await self.storage.finish_reminder(rid)
            except Exception:
                log.exception("reminder loop failed")
            await asyncio.sleep(POLL_INTERVAL)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
