import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    anthropic_api_key: str | None
    model: str = "claude-sonnet-5-5"
    effort: str = "low"
    companion_name: str = "Аня"
    companion_gender: str = "f"  # f | m
    max_tokens: int = 16000
    compact_at: int = 50000  # summarize the dialog once a request reaches this many input tokens (min 50k)
    web_search: bool = True
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    allowed_users: frozenset[int] = field(default_factory=frozenset)  # empty = open to everyone
    db_path: str = "data/bot.db"
    timezone: str = "Europe/Moscow"  # default until the companion learns the user's own
    # Plans: 14 and 30 days, in rubles (YooKassa) and Telegram Stars
    free_messages: int = 30
    rub_14: int = 990
    rub_30: int = 1790
    stars_14: int = 650
    stars_30: int = 1150  # the 30-day plan is a Stars subscription; Telegram has no 14-day ones
    support: str = ""
    # YooKassa (card / SBP / SberPay); disabled while shop id or secret is empty
    yk_shop_id: str = ""
    yk_secret: str = ""
    vat_code: int = 1  # 54-FZ VAT code for receipts; 1 = without VAT
    yk_autopay: bool = False  # needs autopayments enabled for the shop by YooKassa
    # API spend cap per subscriber, so one person can't eat the whole budget
    daily_limit_usd: float = 0.60  # hard cap for any single day
    avg_daily_usd: float = 0.40  # budget for the paid period = days x this
    # The companion writing first
    checkins: bool = True
    checkin_after_hours: int = 20
    checkin_from_hour: int = 10
    checkin_to_hour: int = 21

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN не задан (см. .env.example)")
        effort = os.getenv("EFFORT", "low")
        if effort not in EFFORTS:
            raise SystemExit(f"EFFORT должен быть одним из {EFFORTS}")
        gender = os.getenv("COMPANION_GENDER", "f")
        if gender not in ("f", "m"):
            raise SystemExit("COMPANION_GENDER должен быть f или m")
        return cls(
            telegram_token=token,
            # None -> the SDK falls back to its own credential chain
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
            effort=effort,
            companion_name=os.getenv("COMPANION_NAME", "Аня"),
            companion_gender=gender,
            max_tokens=int(os.getenv("MAX_TOKENS", "16000")),
            compact_at=max(int(os.getenv("COMPACT_AT_TOKENS", "50000")), 50000),
            web_search=_flag("WEB_SEARCH", "1"),
            admin_ids=_ids(os.getenv("ADMIN_IDS", "")),
            allowed_users=_ids(os.getenv("ALLOWED_USERS", "")),
            db_path=os.getenv("DB_PATH", "data/bot.db"),
            timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
            free_messages=int(os.getenv("FREE_MESSAGES", "30")),
            rub_14=int(os.getenv("PRICE_RUB_14", "990")),
            rub_30=int(os.getenv("PRICE_RUB_30", "1790")),
            stars_14=int(os.getenv("PRICE_STARS_14", "650")),
            stars_30=int(os.getenv("PRICE_STARS_30", "1150")),
            support=os.getenv("SUPPORT_CONTACT", ""),
            yk_shop_id=os.getenv("YOOKASSA_SHOP_ID", ""),
            yk_secret=os.getenv("YOOKASSA_SECRET_KEY", ""),
            vat_code=int(os.getenv("YOOKASSA_VAT_CODE", "1")),
            yk_autopay=_flag("YOOKASSA_AUTOPAY", "0"),
            daily_limit_usd=float(os.getenv("DAILY_LIMIT_USD", "0.60")),
            avg_daily_usd=float(os.getenv("AVG_DAILY_LIMIT_USD", "0.40")),
            checkins=_flag("CHECKINS", "1"),
            checkin_after_hours=int(os.getenv("CHECKIN_AFTER_HOURS", "20")),
            checkin_from_hour=int(os.getenv("CHECKIN_FROM_HOUR", "10")),
            checkin_to_hour=int(os.getenv("CHECKIN_TO_HOUR", "21")),
        )

    def plans(self) -> dict[int, "Plan"]:
        return {
            14: Plan(14, "2 недели", self.rub_14, self.stars_14),
            30: Plan(30, "Месяц", self.rub_30, self.stars_30),
        }


@dataclass(frozen=True)
class Plan:
    days: int
    title: str
    rub: int
    stars: int
