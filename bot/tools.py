"""Client tools the companion uses: long-term memory, planned messages, time zone."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import Storage


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TEXT = {"type": "string"}
ID = {"type": "integer"}

TOOLS = [
    _tool(
        "remember",
        "Save a lasting fact about the person to your long-term memory (survives across dialogs): "
        "their name and how to address them, people and pets in their life, work, interests, "
        "upcoming events with dates, what makes them happy or hurts, what helps when they feel "
        "bad, topics to avoid. One fact per call, in Russian, written as a note to yourself.",
        {"fact": TEXT},
        ["fact"],
    ),
    _tool(
        "update_note",
        "Rewrite one of your memory notes that has become outdated. Note ids are shown as #id.",
        {"id": ID, "fact": TEXT},
        ["id", "fact"],
    ),
    _tool("delete_note", "Delete a memory note that is wrong or no longer relevant.", {"id": ID}, ["id"]),
    _tool(
        "schedule_message",
        "Plan to write to the person yourself at a given moment: wish luck before an event, ask "
        "how something went, or deliver a reminder they asked for. At that time you will be "
        "prompted with `reason` and will write the message yourself.",
        {
            "when": {"type": "string", "description": "Person's local date and time, ISO 8601, e.g. 2026-10-21T09:00"},
            "reason": {"type": "string", "description": "What to write about, as a note to yourself"},
        },
        ["when", "reason"],
    ),
    _tool("list_planned", "List the messages you have planned to send this person, with ids.", {}, []),
    _tool("cancel_planned", "Cancel a planned message by id.", {"id": ID}, ["id"]),
    _tool(
        "set_timezone",
        "Set the person's time zone once you learn where they live.",
        {"tz": {"type": "string", "description": "IANA time zone, e.g. Europe/Moscow, Asia/Novosibirsk"}},
        ["tz"],
    ),
]


class Tools:
    def __init__(self, storage: Storage, default_tz: str):
        self.storage = storage
        self.default_tz = default_tz

    async def tz(self, user_id: int) -> ZoneInfo:
        user = await self.storage.user(user_id)
        try:
            return ZoneInfo(user.tz or self.default_tz)
        except ZoneInfoNotFoundError:
            return ZoneInfo(self.default_tz)

    async def run(self, user_id: int, name: str, args: dict) -> str:
        s = self.storage
        if name == "remember":
            return f"Saved as note #{await s.add_note(user_id, _text(args, 'fact'))}."
        if name == "update_note":
            ok = await s.update_note(user_id, _int(args, "id"), _text(args, "fact"))
            return "Updated." if ok else "No such note."
        if name == "delete_note":
            return "Deleted." if await s.delete_note(user_id, _int(args, "id")) else "No such note."

        if name == "schedule_message":
            tz = await self.tz(user_id)
            when = _text(args, "when")
            try:
                due = datetime.fromisoformat(when)
            except ValueError:
                raise ValueError(f"'when' is not ISO 8601: {when!r}") from None
            if due.tzinfo is None:
                due = due.replace(tzinfo=tz)
            if due <= datetime.now(tz):
                raise ValueError("That moment is already in the past.")
            pid = await s.plan(user_id, int(due.timestamp()), _text(args, "reason"))
            return f"Planned #{pid} for {due.astimezone(tz):%Y-%m-%d %H:%M}."
        if name == "list_planned":
            tz = await self.tz(user_id)
            rows = await s.planned(user_id)
            return "\n".join(
                f"#{pid} at {datetime.fromtimestamp(due, timezone.utc).astimezone(tz):%Y-%m-%d %H:%M}: {reason}"
                for pid, due, reason in rows
            ) or "Nothing planned."
        if name == "cancel_planned":
            return "Cancelled." if await s.cancel_plan(user_id, _int(args, "id")) else "No such planned message."

        if name == "set_timezone":
            tz = _text(args, "tz")
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"Unknown time zone {tz!r}; use an IANA name like Europe/Moscow.") from None
            await s.update(user_id, tz=tz)
            return f"Time zone set to {tz}."

        raise ValueError(f"Unknown tool {name!r}.")


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string.")
    return value.strip()


def _int(args: dict, key: str) -> int:
    value = args.get(key)
    if isinstance(value, str) and value.lstrip("#").isdigit():
        value = int(value.lstrip("#"))
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"'{key}' must be an integer id.")
    return value
