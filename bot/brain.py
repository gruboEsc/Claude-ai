import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramForbiddenError

from .claude import Claude, usd
from .companion import build_system, stamp
from .config import Config
from .db import Storage, User
from .tools import Tools

log = logging.getLogger(__name__)

TICK = 60  # seconds between checks for planned messages and check-ins
MAX_BUBBLES = 4
TG_LIMIT = 4096


class Companion:
    def __init__(self, cfg: Config, storage: Storage, claude: Claude, tools: Tools):
        self.cfg = cfg
        self.storage = storage
        self.claude = claude
        self.tools = tools
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def has_access(self, user: User) -> bool:
        return (
            user.user_id in self.cfg.admin_ids
            or user.paid_until > time.time()
            or user.used < self.cfg.free_messages
        )

    def subscribed(self, user: User) -> bool:
        return user.user_id in self.cfg.admin_ids or user.paid_until > time.time()

    async def over_limit(self, user: User) -> tuple[str, datetime] | None:
        """('day' | 'period', when it resets) once a subscriber has used up their API budget."""
        now = time.time()
        if user.user_id in self.cfg.admin_ids or user.paid_until <= now:
            return None  # admins are unlimited; the trial is limited by message count instead
        tz = await self.tools.tz(user.user_id)
        today = datetime.now(tz)
        if await self.storage.spent(user.user_id, today.date().isoformat()) >= self.cfg.daily_limit_usd:
            tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return "day", tomorrow
        # Budget for the current paid period; when several are paid ahead, the latest stretch
        # of the same length.
        period = user.plan_days * 86400
        start = user.paid_until - period
        if start > now:
            start = now - period
        start_day = datetime.fromtimestamp(start, tz)
        budget = user.plan_days * self.cfg.avg_daily_usd
        if await self.storage.spent(user.user_id, start_day.date().isoformat()) >= budget:
            return "period", start_day + timedelta(seconds=period)
        return None

    async def _system(self, user: User, refresh: bool = False) -> str:
        # Frozen per dialog so every request shares one cached prefix; rebuilt with the
        # latest notes when a dialog starts and after server-side compaction.
        if user.system and not refresh:
            return user.system
        tz = await self.tools.tz(user.user_id)
        system = build_system(
            self.cfg.companion_name, self.cfg.companion_gender, await self.storage.notes(user.user_id), str(tz)
        )
        await self.storage.update(user.user_id, system=system)
        return system

    async def converse(self, bot: Bot, user_id: int, content: list[dict], proactive: bool = False) -> bool:
        """Answer one user turn (a real message or a service note). False if nothing was sent."""
        async with self.locks[user_id]:
            user = await self.storage.user(user_id)
            system = await self._system(user)
            now = datetime.now(await self.tools.tz(user_id))
            user_turn = {"role": "user", "content": [{"type": "text", "text": stamp(now)}, *content]}
            history = await self.storage.history(user_id) + [user_turn]

            async def run_tool(name: str, args: dict) -> str:
                return await self.tools.run(user_id, name, args)

            typing = asyncio.create_task(_keep_typing(bot, user_id))
            try:
                reply = await self.claude.reply(system, history, self.cfg.effort, run_tool)
            finally:
                typing.cancel()

            await self.storage.add_usage(user_id, reply.input_tokens, reply.output_tokens, reply.cached_tokens)
            await self.storage.add_spend(
                user_id, now.date().isoformat(),
                usd(self.cfg.model, reply.input_tokens, reply.output_tokens, reply.cached_tokens),
            )
            if reply.refused:
                # Not saved, so the dialog continues as if this turn never happened.
                if proactive:
                    return False
                return await self._send(bot, user_id, ["Давай об этом не будем 🙂 Расскажи лучше, как ты?"])

            await self.storage.append(user_id, [user_turn, *reply.turns])
            if reply.compacted:
                await self._system(user, refresh=True)
            text = reply.text.strip()
            if not text:
                return False
            return await self._send(bot, user_id, bubbles(text))

    async def _send(self, bot: Bot, user_id: int, parts: list[str]) -> bool:
        try:
            for i, part in enumerate(parts):
                if i:
                    # Type each follow-up for a moment, like a person sending several messages.
                    await bot.send_chat_action(user_id, ChatAction.TYPING)
                    await asyncio.sleep(min(1.0 + len(part) / 40, 4.0))
                await bot.send_message(user_id, part, parse_mode=None)
        except TelegramForbiddenError:
            await self.storage.update(user_id, blocked=1)
            return False
        await self.storage.update(user_id, last_bot_at=int(time.time()))
        return True

    async def write_first(self, bot: Bot, user_id: int, note: str) -> None:
        try:
            await self.converse(bot, user_id, [{"type": "text", "text": note}], proactive=True)
        except Exception:
            log.exception("proactive message to %s failed", user_id)

    async def run_forever(self, bot: Bot) -> None:
        while True:
            try:
                await self.tick(bot)
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK)

    async def tick(self, bot: Bot) -> None:
        now = int(time.time())
        for pid, user_id, reason in await self.storage.due_plans(now):
            await self.storage.finish_plan(pid)
            user = await self.storage.user(user_id)
            if user.blocked or not self.has_access(user) or await self.over_limit(user):
                continue
            await self.write_first(bot, user_id, f"[Служебно: пора написать человеку самому. Повод: {reason}]")

        if not self.cfg.checkins:
            return
        idle = self.cfg.checkin_after_hours * 3600
        for user in await self.storage.checkin_candidates(now, idle):
            hour = datetime.now(await self.tools.tz(user.user_id)).hour
            if not self.cfg.checkin_from_hour <= hour < self.cfg.checkin_to_hour or await self.over_limit(user):
                continue
            await self.storage.update(user.user_id, unanswered=user.unanswered + 1)
            hours = (now - user.last_user_at) // 3600
            await self.write_first(
                bot,
                user.user_id,
                f"[Служебно: человек не писал около {hours} ч. Напиши первым — коротко и тепло, "
                "без упрёков и без вопросов, почему пропал. Можно вспомнить что-то из ваших "
                "разговоров или просто поделиться мыслью.]",
            )


def bubbles(text: str) -> list[str]:
    """Split a reply into separate messages on blank lines, the way people text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # stray markdown would show literally
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) > MAX_BUBBLES:
        parts = parts[: MAX_BUBBLES - 1] + ["\n\n".join(parts[MAX_BUBBLES - 1:])]
    out = []
    for part in parts:
        while len(part) > TG_LIMIT:
            cut = part.rfind("\n", 0, TG_LIMIT)
            cut = cut if cut > 0 else TG_LIMIT
            out.append(part[:cut].rstrip())
            part = part[cut:].lstrip()
        out.append(part)
    return out


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.debug("typing indicator failed", exc_info=True)
