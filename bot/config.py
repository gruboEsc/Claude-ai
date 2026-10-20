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
    model: str = "claude-opus-5-5"
    effort: str = "low"
    companion_name: str = "Аня"
    companion_gender: str = "f"  # f | m
    max_tokens: int = 16000
    web_search: bool = True
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    allowed_users: frozenset[int] = field(default_factory=frozenset)  # empty = open to everyone
    db_path: str = "data/bot.db"
    timezone: str = "Europe/Moscow"  # default until the companion learns the user's own
    # Subscription (Telegram Stars)
    free_messages: int = 30
    price_stars: int = 499  # per 30 days; Telegram subscriptions are always monthly
    support: str = ""
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
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            effort=effort,
            companion_name=os.getenv("COMPANION_NAME", "Аня"),
            companion_gender=gender,
            max_tokens=int(os.getenv("MAX_TOKENS", "16000")),
            web_search=_flag("WEB_SEARCH", "1"),
            admin_ids=_ids(os.getenv("ADMIN_IDS", "")),
            allowed_users=_ids(os.getenv("ALLOWED_USERS", "")),
            db_path=os.getenv("DB_PATH", "data/bot.db"),
            timezone=os.getenv("TIMEZONE", "Europe/Moscow"),
            free_messages=int(os.getenv("FREE_MESSAGES", "30")),
            price_stars=int(os.getenv("PRICE_STARS", "499")),
            support=os.getenv("SUPPORT_CONTACT", ""),
            checkins=_flag("CHECKINS", "1"),
            checkin_after_hours=int(os.getenv("CHECKIN_AFTER_HOURS", "20")),
            checkin_from_hour=int(os.getenv("CHECKIN_FROM_HOUR", "10")),
            checkin_to_hour=int(os.getenv("CHECKIN_TO_HOUR", "21")),
        )
