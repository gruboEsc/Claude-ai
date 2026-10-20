import asyncio
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta

from bot.billing import SUB_PERIOD, Billing
from bot.brain import Companion
from bot.config import Config
from bot.db import Storage
from bot.tools import Tools
from tests.test_companion import FakeBot, FakeClaude

CFG = Config(telegram_token="x", anthropic_api_key="k", web_search=False,
             daily_limit_usd=0.40, monthly_limit_usd=1.00)


def run(coro):
    return asyncio.run(coro)


class FakeYK:
    def __init__(self):
        self.created = []
        self.status = {}

    async def create_payment(self, **kw):
        pid = f"p{len(self.created) + 1}"
        self.created.append(kw)
        self.status[pid] = "pending"
        return {"id": pid, "status": "pending", "amount": {"value": "490.00"},
                "confirmation": {"confirmation_url": f"https://yoomoney.ru/pay/{pid}"}}

    async def get_payment(self, pid):
        return {"id": pid, "status": self.status[pid],
                "payment_method": {"id": "card-1", "saved": True}}


class Me:
    username = "companion_bot"


class Bot(FakeBot):
    async def get_me(self):
        return Me()


async def _storage(tmp_path) -> Storage:
    storage = Storage(str(tmp_path / "bot.db"))
    await storage.open()
    return storage


def test_payment_by_link_extends_once(tmp_path):
    async def scenario():
        storage = await _storage(tmp_path)
        yk = FakeYK()
        billing = Billing(CFG, storage, yk)
        bot = Bot()
        await storage.user(1)
        url = await billing.start(bot, 1, "a@b.ru")
        assert url.endswith("/p1")
        assert yk.created[0]["email"] == "a@b.ru" and yk.created[0]["rub"] == 490
        assert yk.created[0]["return_url"] == "https://t.me/companion_bot"

        assert not await billing.check_user(bot, 1)  # still pending
        yk.status["p1"] = "succeeded"
        assert await billing.check_user(bot, 1)
        paid_until = (await storage.user(1)).paid_until
        assert abs(paid_until - (time.time() + SUB_PERIOD)) < 5
        await billing.tick(bot)  # already settled: no second extension
        assert (await storage.user(1)).paid_until == paid_until
        assert "Спасибо" in bot.sent[0][1] and len(bot.sent) == 1
        # autopay is off by default, so the card is not kept
        assert (await storage.user(1)).autopay == 0
        await storage.close()

    run(scenario())


def test_autopay_renewal_and_decline(tmp_path):
    async def scenario():
        storage = await _storage(tmp_path)
        yk = FakeYK()
        billing = Billing(replace(CFG, yk_autopay=True), storage, yk)
        bot = Bot()
        await storage.user(2)
        await storage.update(2, email="c@d.ru")
        await billing.start(bot, 2, "c@d.ru")
        assert yk.created[0]["save_method"] is True
        yk.status["p1"] = "succeeded"
        await billing.tick(bot)
        user = await storage.user(2)
        assert (user.autopay, user.pay_method_id) == (1, "card-1")

        # near the end of the period the saved card is charged, once
        await storage.update(2, paid_until=int(time.time()) + 3600)
        await billing.tick(bot)
        await billing.tick(bot)
        renewals = [c for c in yk.created if c.get("method_id")]
        assert len(renewals) == 1 and renewals[0]["method_id"] == "card-1"

        yk.status["p2"] = "canceled"
        await billing.tick(bot)
        user = await storage.user(2)
        assert (user.autopay, user.pay_method_id) == (0, None)
        assert "Не получилось продлить" in bot.sent[-1][1]
        await storage.close()

    run(scenario())


def test_spend_limits(tmp_path):
    async def scenario():
        storage = await _storage(tmp_path)
        companion = Companion(CFG, storage, FakeClaude(), Tools(storage, CFG.timezone))
        now = int(time.time())
        await storage.user(3)
        user = await storage.user(3)
        assert await companion.over_limit(user) is None  # trial: limited by message count

        await storage.update(3, paid_until=now + SUB_PERIOD - 86400 * 5)
        user = await storage.user(3)
        today = datetime.now(await companion.tools.tz(3)).date()
        await storage.add_spend(3, today.isoformat(), 0.39)
        assert await companion.over_limit(user) is None
        await storage.add_spend(3, today.isoformat(), 0.02)
        assert (await companion.over_limit(user))[0] == "day"

        # earlier days of this paid month count toward the monthly cap
        await storage.add_spend(3, (today - timedelta(days=2)).isoformat(), 0.70)
        await storage.add_spend(3, (today - timedelta(days=60)).isoformat(), 5.00)  # previous period
        await storage.conn.execute("DELETE FROM spend WHERE user_id = 3 AND day = ?", (today.isoformat(),))
        assert await companion.over_limit(user) is None  # 0.70 < 1.00
        await storage.add_spend(3, today.isoformat(), 0.31)
        assert (await companion.over_limit(user))[0] == "month"

        bot = FakeBot()
        await companion.converse(bot, 3, [{"type": "text", "text": "hi"}])
        assert await storage.spent(3, today.isoformat()) >= 0.31
        await storage.update(3, paid_until=now - 1)
        assert await companion.over_limit(await storage.user(3)) is None
        await storage.close()

    run(scenario())


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, tz TEXT, used INTEGER NOT NULL DEFAULT 0, "
        "paid_until INTEGER NOT NULL DEFAULT 0, charge_id TEXT, last_user_at INTEGER NOT NULL DEFAULT 0, "
        "last_bot_at INTEGER NOT NULL DEFAULT 0, unanswered INTEGER NOT NULL DEFAULT 0, "
        "checkins INTEGER NOT NULL DEFAULT 1, blocked INTEGER NOT NULL DEFAULT 0, system TEXT, "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
        "cached_tokens INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute("INSERT INTO users (user_id, used) VALUES (5, 7)")
    con.commit()
    con.close()

    async def scenario():
        storage = Storage(str(path))
        await storage.open()
        user = await storage.user(5)
        assert user.used == 7 and user.email is None and user.autopay == 0
        await storage.close()

    run(scenario())
