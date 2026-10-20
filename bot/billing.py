"""Subscription payments through YooKassa: first payment by link, renewals by saved card.

There is no webhook server: pending payments are polled, and the person can press
"I paid" to have theirs checked right away.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import Config, Plan
from .db import Storage
from .i18n import companion_name
from .i18n import companion_name
from .yookassa import YooKassa, YooKassaError

log = logging.getLogger(__name__)

TICK = 60
PENDING_TTL = 2 * 86400  # stop polling a payment nobody completed
RENEW_AHEAD = 86400  # charge a saved card this long before the paid period ends


class Billing:
    def __init__(self, cfg: Config, storage: Storage, yk: YooKassa | None):
        self.cfg = cfg
        self.storage = storage
        self.yk = yk

    @property
    def enabled(self) -> bool:
        return self.yk is not None

    def description(self, plan: Plan) -> str:
        name = companion_name("ru", self.cfg.companion_gender, self.cfg.companion_name)
        return f"Подписка на общение с {name}, {plan.days} дней"

    async def start(self, bot: Bot, user_id: int, email: str, plan: Plan) -> str:
        """Create a payment and return the YooKassa page to pay on."""
        me = await bot.get_me()
        payment = await self.yk.create_payment(
            rub=plan.rub,
            description=self.description(plan),
            user_id=user_id,
            email=email,
            vat_code=self.cfg.vat_code,
            return_url=f"https://t.me/{me.username}",
            save_method=self.cfg.yk_autopay,
        )
        await self.storage.add_yk_payment(
            payment["id"], user_id, payment["amount"]["value"], payment["status"], False, plan.days
        )
        return payment["confirmation"]["confirmation_url"]

    async def check_user(self, bot: Bot, user_id: int) -> bool:
        """Check this person's pending payments now; True if one went through."""
        paid = False
        for pid, _, renewal, _, days in await self.storage.pending_yk(user_id):
            paid |= await self._check(bot, pid, user_id, bool(renewal), days) == "succeeded"
        return paid

    async def _check(self, bot: Bot, pid: str, user_id: int, renewal: bool, days: int) -> str:
        payment = await self.yk.get_payment(pid)
        status = payment["status"]
        if status not in ("succeeded", "canceled") or not await self.storage.settle_yk(pid, status):
            return status

        if status == "succeeded":
            until = await self.storage.extend(user_id, days, int(time.time()))
            method = payment.get("payment_method") or {}
            if self.cfg.yk_autopay and method.get("saved"):
                await self.storage.update(user_id, pay_method_id=method["id"], autopay=1)
            date = datetime.fromtimestamp(until, timezone.utc).strftime("%d.%m.%Y")
            text = (f"Подписка продлена до {date} ✨" if renewal
                    else f"Спасибо! Оплата прошла, подписка активна до {date} ✨")
            await self._notify(bot, user_id, text)
        elif renewal:
            # The saved card was declined: stop retrying it and let the person pay by hand.
            await self.storage.update(user_id, autopay=0, pay_method_id=None)
            await self._notify(bot, user_id, "Не получилось продлить подписку с сохранённой карты. "
                                             "Продлить вручную можно в /status.")
        return status

    async def _notify(self, bot: Bot, user_id: int, text: str) -> None:
        try:
            await bot.send_message(user_id, text)
        except TelegramAPIError as e:
            log.warning("payment notice to %s failed: %s", user_id, e)

    async def tick(self, bot: Bot) -> None:
        now = int(time.time())
        for pid, user_id, renewal, created_at, days in await self.storage.pending_yk():
            if now - created_at > PENDING_TTL:
                await self.storage.settle_yk(pid, "expired")
                continue
            try:
                await self._check(bot, pid, user_id, bool(renewal), days)
            except YooKassaError as e:
                log.warning("checking payment %s failed: %s", pid, e)

        if not self.cfg.yk_autopay:
            return
        for user in await self.storage.renewal_candidates(now, RENEW_AHEAD):
            if not user.email:
                continue
            # Renew the plan the person last chose.
            plan = self.cfg.plans().get(user.plan_days) or self.cfg.plans()[30]
            try:
                payment = await self.yk.create_payment(
                    rub=plan.rub,
                    description=self.description(plan),
                    user_id=user.user_id,
                    email=user.email,
                    vat_code=self.cfg.vat_code,
                    method_id=user.pay_method_id,
                )
            except YooKassaError as e:
                log.warning("renewal for %s failed: %s", user.user_id, e)
                continue
            await self.storage.add_yk_payment(
                payment["id"], user.user_id, payment["amount"]["value"], "pending", True, plan.days
            )

    async def run_forever(self, bot: Bot) -> None:
        while True:
            try:
                await self.tick(bot)
            except Exception:
                log.exception("billing tick failed")
            await asyncio.sleep(TICK)
