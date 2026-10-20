import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    telegram_token: str
    anthropic_api_key: str | None
    model: str = "claude-opus-5-5"
    default_effort: str = "medium"
    max_tokens: int = 32000
    web_search: bool = True
    allowed_users: frozenset[int] = field(default_factory=frozenset)
    db_path: str = "data/bot.db"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN не задан (см. .env.example)")
        effort = os.getenv("DEFAULT_EFFORT", "medium")
        if effort not in EFFORTS:
            raise SystemExit(f"DEFAULT_EFFORT должен быть одним из {EFFORTS}")
        return cls(
            telegram_token=token,
            # None -> the SDK falls back to its own credential chain
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            default_effort=effort,
            max_tokens=int(os.getenv("MAX_TOKENS", "32000")),
            web_search=os.getenv("WEB_SEARCH", "1") not in ("0", "false", "no"),
            allowed_users=_ids(os.getenv("ALLOWED_USERS", "")),
            db_path=os.getenv("DB_PATH", "data/bot.db"),
        )
