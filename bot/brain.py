import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .channels import Channel
from .claude import Claude, usd
from .companion import build_system, stamp
from .i18n import LANGS, companion_name, lang_of, t
from .config import Config
from .db import Storage, User
from .tools import Tools

log = logging.getLogger(__name__)

TICK = 60  # seconds between checks for planned messages and check-ins
MAX_BUBBLES = 4
TG_LIMIT = 4096


class Companion:
    def __init__(self, cfg: Config, storage: Storage, claude: Claude, tools: Tools, channels: list[Channel]):
        self.cfg = cfg
        self.storage = storage
        self.claude = claude
        self.tools = tools
        self.channels = {channel.name: channel for channel in channels}
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def has_access(self, user: User) -> bool:
        return (
            user.user_id in self.cfg.admin_ids
            or user.paid_until > time.time()
            or user.used < self.cfg.free_messages
        )

    def subscribed(self, user: User) -> bool:
        return user.user_id in self.cfg.admin_ids or user.paid_until > time.time()

    @staticmethod
    def lang(user: User) -> str:
        return lang_of(user.lang)

    def tier(self, user: User) -> int:
        return LANGS[self.lang(user)].tier

    def name(self, user: User) -> str:
        return companion_name(self.lang(user), self.cfg.companion_gender, self.cfg.companion_name)

    async def over_limit(self, user: User) -> tuple[str, datetime] | None:
        """('day' | 'period', when it resets) once a subscriber has used up their API budget."""
        now = time.time()
        if user.user_id in self.cfg.admin_ids or user.paid_until <= now:
            return None  # admins are unlimited; the trial is limited by message count instead
        daily, avg_daily = self.cfg.limits(self.tier(user))
        tz = await self.tools.tz(user.user_id)
        today = datetime.now(tz)
        if await self.storage.spent(user.user_id, today.date().isoformat()) >= daily:
            tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return "day", tomorrow
        # Budget for the current paid period; when several are paid ahead, the latest stretch
        # of the same length.
        period = user.plan_days * 86400
        start = user.paid_until - period
        if start > now:
            start = now - period
        start_day = datetime.fromtimestamp(start, tz)
        budget = user.plan_days * avg_daily
        if await self.storage.spent(user.user_id, start_day.date().isoformat()) >= budget:
            return "period", start_day + timedelta(seconds=period)
        return None

    async def admit(self, user: User) -> tuple[str, dict] | None:
        """Why this person can't get a reply right now, as an i18n key and its fields; None if they can."""
        if not self.has_access(user):
            return "paywall", {}
        limit = await self.over_limit(user)
        if limit:
            kind, resets = limit
            return ("limit_day", {}) if kind == "day" else ("limit_period", {"date": f"{resets:%d.%m}"})
        return None

    async def _system(self, user: User, refresh: bool = False) -> str:
        # Frozen per dialog so every request shares one cached prefix; rebuilt with the
        # latest notes when a dialog starts and after server-side compaction.
        if user.system and not refresh:
            return user.system
        tz = await self.tools.tz(user.user_id)
        system = build_system(
            self.name(user), self.cfg.companion_gender, self.lang(user), await self.storage.notes(user.user_id), str(tz)
        )
        await self.storage.update(user.user_id, system=system)
        return system

    async def converse(
        self, user_id: int, content: list[dict], proactive: bool = False, prefix: list[str] | None = None
    ) -> bool:
        """Answer one user turn (a real message or a service note). False if nothing was sent.

        `prefix` messages go out first, in the same delivery (on LINE that keeps them in the
        free reply instead of a paid push).
        """
        prefix = prefix or []
        async with self.locks[user_id]:
            user = await self.storage.user(user_id)
            system = await self._system(user)
            now = datetime.now(await self.tools.tz(user_id))
            user_turn = {"role": "user", "content": [{"type": "text", "text": stamp(now)}, *content]}
            history = await self.storage.history(user_id) + [user_turn]

            async def run_tool(name: str, args: dict) -> str:
                return await self.tools.run(user_id, name, args)

            typing = asyncio.create_task(self._keep_typing(user))
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
                    return await self.send(user, prefix) if prefix else False
                return await self.send(user, [*prefix, t(self.lang(user), "refusal")])

            await self.storage.append(user_id, [user_turn, *reply.turns])
            if reply.compacted:
                await self._system(user, refresh=True)
            parts = [*prefix, *bubbles(reply.text.strip())] if reply.text.strip() else prefix
            if not parts:
                return False
            return await self.send(user, parts)

    async def send(self, user: User, parts: list[str]) -> bool:
        """Deliver messages through the person's messenger; marks them blocked if it refuses."""
        channel = self.channels.get(user.platform)
        if channel is None:
            log.warning("no %s channel configured, can't reach user %s", user.platform, user.user_id)
            return False
        if not await channel.send(user.external_id, parts):
            await self.storage.update(user.user_id, blocked=1)
            return False
        await self.storage.update(user.user_id, last_bot_at=int(time.time()))
        return True

    async def notify(self, user_id: int, text: str) -> None:
        """A one-off service message (e.g. about a payment) outside the dialog."""
        await self.send(await self.storage.user(user_id), [text])

    async def _keep_typing(self, user: User) -> None:
        channel = self.channels.get(user.platform)
        if channel is None:
            return
        try:
            while True:
                await channel.typing(user.external_id)
                await asyncio.sleep(4.5)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("typing indicator failed", exc_info=True)

    async def write_first(self, user_id: int, note: str) -> None:
        try:
            await self.converse(user_id, [{"type": "text", "text": note}], proactive=True)
        except Exception:
            log.exception("proactive message to %s failed", user_id)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK)

    async def tick(self) -> None:
        now = int(time.time())
        for pid, user_id, reason in await self.storage.due_plans(now):
            await self.storage.finish_plan(pid)
            user = await self.storage.user(user_id)
            if user.blocked or not self.has_access(user) or await self.over_limit(user):
                continue
            await self.write_first(user_id, f"[Service note: time to write to the person yourself. Reason: {reason}]")

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
                user.user_id,
                f"[Service note: the person hasn't written for about {hours} h. Write first — briefly and "
                "warmly, without reproach and without asking why they disappeared. You can recall something "
                "from your talks or just share a thought.]",
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

