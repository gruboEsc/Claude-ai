import asyncio
import string
import time
from datetime import datetime
from types import SimpleNamespace

from bot.billing import Billing
from bot.brain import Companion
from bot.companion import build_system
from bot.config import Config
from bot.db import Storage
from bot.handlers import setup
from bot.i18n import LANGS, STRINGS, companion_name, lang_of, t
from bot.tools import Tools
from tests.test_companion import FakeBot, FakeClaude

# Shown to every user, so every language must have its own text.
EVERYONE = [
    "closed", "back", "intro", "paywall", "plan_14", "plan_30", "invoice_desc", "invoice_renews",
    "status_active", "btn_cancel_renew", "btn_renew_early", "status_trial", "subscribe", "unsub_done",
    "thanks_paid", "settings", "btn_checkins_on", "btn_checkins_off", "toggled_on", "toggled_off",
    "forget_confirm", "btn_yes_erase", "btn_no", "forgot", "kept", "paysupport", "paysupport_contact",
    "help", "voice", "bad_file", "busy", "api_error", "limit_day", "limit_period", "refusal",
    "language_pick", "language_set",
]


def test_every_language_has_the_everyday_texts():
    for key in EVERYONE:
        missing = set(LANGS) - set(STRINGS[key])
        assert not missing, f"{key} missing {missing}"


def test_all_texts_format_with_their_fields():
    for key, texts in STRINGS.items():
        for lang, text in texts.items():
            fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
            t(lang, key, **{f: "x" for f in fields})  # raises on a broken placeholder


def test_language_detection_and_names():
    assert lang_of("pt-br") == "en" and lang_of("zh-hans") == "zh" and lang_of(None) == "en"
    assert lang_of("ja") == "ja" and lang_of("ID") == "id"
    assert companion_name("ja", "f") == "ユイ" and companion_name("ko", "m") == "민준"
    assert companion_name("ru", "f", override="Катя") == "Катя"
    assert companion_name("en", "f", override="Катя") == "Mia"  # override is for Russian only
    for code in LANGS:
        assert build_system(companion_name(code, "f"), "f", code, [], "UTC")


class Bot:
    def __init__(self):
        self.links = []

    async def create_invoice_link(self, **kw):
        self.links.append(kw)
        return f"https://t.me/$inv{len(self.links)}"


def _keyboard_for(tmp_path, lang):
    async def scenario():
        cfg = Config(telegram_token="x", anthropic_api_key="k")
        storage = Storage(str(tmp_path / f"{lang}.db"))
        await storage.open()
        companion = Companion(cfg, storage, FakeClaude(), Tools(storage, cfg.timezone), [FakeBot()])
        router = setup(cfg, companion, Billing(cfg, storage, object()))
        handler = next(h.callback for h in router.message.handlers if h.callback.__name__ == "cmd_subscribe")
        out = {}

        async def answer(text, reply_markup=None):
            out["text"], out["kb"] = text, reply_markup

        bot = Bot()
        tg = SimpleNamespace(id=1, language_code=lang)
        await handler(SimpleNamespace(answer=answer, from_user=tg), bot)
        await storage.close()
        return out, bot.links

    return asyncio.run(scenario())


def test_russian_users_see_rubles_and_stars(tmp_path):
    out, links = _keyboard_for(tmp_path, "ru")
    buttons = [b.text for row in out["kb"].inline_keyboard for b in row]
    assert buttons == ["💳 2 недели — 990 ₽", "⭐ 2 недели — 650", "💳 Месяц — 1790 ₽", "⭐ Месяц — 1150"]
    assert [(link["payload"], link["subscription_period"]) for link in links] == [("plan14", None), ("plan30", 2592000)]


def test_lower_income_markets_get_tier_two_prices_and_no_yookassa(tmp_path):
    out, links = _keyboard_for(tmp_path, "id")
    buttons = [b.text for row in out["kb"].inline_keyboard for b in row]
    assert buttons == ["⭐ 2 minggu — 300", "⭐ 1 bulan — 500"]
    assert "Paket" in out["text"] and "₽" not in out["text"]
    assert links[1]["title"] == "Putri: 1 bulan"


def test_japanese_users_get_standard_stars_prices(tmp_path):
    out, _ = _keyboard_for(tmp_path, "ja")
    assert [b.text for row in out["kb"].inline_keyboard for b in row] == ["⭐ 2週間 — 650", "⭐ 1か月 — 1150"]


def test_tier_two_spend_limits(tmp_path):
    async def scenario():
        cfg = Config(telegram_token="x", anthropic_api_key="k")
        storage = Storage(str(tmp_path / "t.db"))
        await storage.open()
        companion = Companion(cfg, storage, FakeClaude(), Tools(storage, cfg.timezone), [FakeBot()])
        await storage.user(1)
        await storage.update(1, lang="hi", paid_until=int(time.time()) + 86400 * 20, plan_days=30)
        user = await storage.user(1)
        today = datetime.now(await companion.tools.tz(1)).date()
        await storage.add_spend(1, today.isoformat(), 0.19)
        assert await companion.over_limit(user) is None
        await storage.add_spend(1, today.isoformat(), 0.02)  # tier 2 daily cap is $0.20
        assert (await companion.over_limit(user))[0] == "day"
        await storage.update(1, lang="ru")
        assert await companion.over_limit(await storage.user(1)) is None  # tier 1 cap is $0.50
        await storage.close()

    asyncio.run(scenario())
