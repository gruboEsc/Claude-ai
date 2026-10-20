import json
import os
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    persona TEXT,
    effort TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    due INTEGER NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(done, due);
"""


@dataclass
class ChatSettings:
    persona: str | None
    effort: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    requests: int
    turns: int


class Storage:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if os.path.dirname(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def history(self, chat_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)
        )
        return [{"role": role, "content": json.loads(content)} for role, content in await cur.fetchall()]

    async def append(self, chat_id: int, turns: list[dict]) -> None:
        # History is append-only: earlier turns are never rewritten, which keeps
        # the prompt cache warm and replayed thinking blocks valid.
        await self.conn.executemany(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            [(chat_id, t["role"], json.dumps(t["content"], ensure_ascii=False)) for t in turns],
        )
        await self.conn.commit()

    async def reset(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def settings(self, chat_id: int) -> ChatSettings:
        cur = await self.conn.execute(
            "SELECT persona, effort, input_tokens, output_tokens, cached_tokens, requests "
            "FROM chats WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cur.fetchone() or (None, None, 0, 0, 0, 0)
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND role = 'user'", (chat_id,)
        )
        (turns,) = await cur.fetchone()
        return ChatSettings(*row, turns=turns)

    async def set_field(self, chat_id: int, field: str, value: str) -> None:
        assert field in ("persona", "effort")
        await self.conn.execute(
            f"INSERT INTO chats (chat_id, {field}) VALUES (?, ?) "
            f"ON CONFLICT(chat_id) DO UPDATE SET {field} = excluded.{field}",
            (chat_id, value),
        )
        await self.conn.commit()

    async def add_reminder(self, chat_id: int, due: int, text: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reminders (chat_id, due, text) VALUES (?, ?, ?)", (chat_id, due, text)
        )
        await self.conn.commit()
        return cur.lastrowid

    async def pending_reminders(self, chat_id: int) -> list[tuple[int, int, str]]:
        cur = await self.conn.execute(
            "SELECT id, due, text FROM reminders WHERE chat_id = ? AND done = 0 ORDER BY due", (chat_id,)
        )
        return await cur.fetchall()

    async def cancel_reminder(self, chat_id: int, rid: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE reminders SET done = 1 WHERE id = ? AND chat_id = ? AND done = 0", (rid, chat_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def due_reminders(self, now: int) -> list[tuple[int, int, str]]:
        cur = await self.conn.execute(
            "SELECT id, chat_id, text FROM reminders WHERE done = 0 AND due <= ? ORDER BY due", (now,)
        )
        return await cur.fetchall()

    async def finish_reminder(self, rid: int) -> None:
        await self.conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
        await self.conn.commit()

    async def add_usage(self, chat_id: int, inp: int, out: int, cached: int) -> None:
        await self.conn.execute(
            "INSERT INTO chats (chat_id, input_tokens, output_tokens, cached_tokens, requests) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT(chat_id) DO UPDATE SET "
            "input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens, "
            "cached_tokens = cached_tokens + excluded.cached_tokens, "
            "requests = requests + 1",
            (chat_id, inp, out, cached),
        )
        await self.conn.commit()
