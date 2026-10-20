import json
import os
from dataclasses import dataclass

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    tz TEXT,
    used INTEGER NOT NULL DEFAULT 0,           -- free messages spent
    paid_until INTEGER NOT NULL DEFAULT 0,     -- unix time
    charge_id TEXT,                            -- last subscription payment
    last_user_at INTEGER NOT NULL DEFAULT 0,
    last_bot_at INTEGER NOT NULL DEFAULT 0,
    unanswered INTEGER NOT NULL DEFAULT 0,     -- check-ins sent since the person last wrote
    checkins INTEGER NOT NULL DEFAULT 1,
    blocked INTEGER NOT NULL DEFAULT 0,        -- the person blocked the bot
    system TEXT,                               -- system prompt frozen for the current dialog
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)),
    email TEXT,                                -- for YooKassa receipts
    pay_method_id TEXT,                        -- saved YooKassa method for renewals
    autopay INTEGER NOT NULL DEFAULT 0,
    awaiting_email INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dialog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dialog_user ON dialog(user_id, id);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planned (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    due INTEGER NOT NULL,
    reason TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS planned_due ON planned(done, due);
CREATE TABLE IF NOT EXISTS payments (
    charge_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    recurring INTEGER NOT NULL,
    paid_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);
CREATE TABLE IF NOT EXISTS yk_payments (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount TEXT NOT NULL,
    status TEXT NOT NULL,
    renewal INTEGER NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);
CREATE INDEX IF NOT EXISTS yk_pending ON yk_payments(status);
CREATE TABLE IF NOT EXISTS spend (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,                         -- person's local date, YYYY-MM-DD
    usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
"""

# Columns added after the first release, appended to existing databases in this order.
USER_MIGRATIONS = [
    ("email", "TEXT"),
    ("pay_method_id", "TEXT"),
    ("autopay", "INTEGER NOT NULL DEFAULT 0"),
    ("awaiting_email", "INTEGER NOT NULL DEFAULT 0"),
]

MAX_NOTES = 150


@dataclass
class User:
    user_id: int
    tz: str | None
    used: int
    paid_until: int
    charge_id: str | None
    last_user_at: int
    last_bot_at: int
    unanswered: int
    checkins: int
    blocked: int
    system: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    created_at: int
    email: str | None
    pay_method_id: str | None
    autopay: int
    awaiting_email: int


class Storage:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if os.path.dirname(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript(SCHEMA)
        have = {row[1] for row in await self._all("PRAGMA table_info(users)")}
        for col, decl in USER_MIGRATIONS:
            if col not in have:
                await self.conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _exec(self, sql: str, args: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur

    async def _all(self, sql: str, args: tuple = ()) -> list[tuple]:
        cur = await self.conn.execute(sql, args)
        return await cur.fetchall()

    # users

    async def user(self, user_id: int) -> User:
        await self._exec("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return User(*await cur.fetchone())

    async def is_new(self, user_id: int) -> bool:
        return not await self._all("SELECT 1 FROM users WHERE user_id = ?", (user_id,))

    async def update(self, user_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self._exec(f"UPDATE users SET {cols} WHERE user_id = ?", (*fields.values(), user_id))

    async def add_usage(self, user_id: int, inp: int, out: int, cached: int) -> None:
        await self._exec(
            "UPDATE users SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
            "cached_tokens = cached_tokens + ? WHERE user_id = ?",
            (inp, out, cached, user_id),
        )

    async def user_wrote(self, user_id: int, now: int, counted: bool) -> None:
        await self._exec(
            "UPDATE users SET last_user_at = ?, unanswered = 0, blocked = 0, used = used + ? WHERE user_id = ?",
            (now, int(counted), user_id),
        )

    async def checkin_candidates(self, now: int, idle: int) -> list[User]:
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE checkins = 1 AND blocked = 0 AND paid_until > ? "
            "AND last_user_at > 0 AND last_user_at < ? AND last_bot_at < ? AND unanswered < 2",
            (now, now - idle, now - idle),
        )
        return [User(*row) for row in await cur.fetchall()]

    # dialog — append-only: earlier turns are never rewritten, which keeps the prompt
    # cache warm and replayed thinking blocks valid.

    async def history(self, user_id: int) -> list[dict]:
        rows = await self._all("SELECT role, content FROM dialog WHERE user_id = ? ORDER BY id", (user_id,))
        return [{"role": role, "content": json.loads(content)} for role, content in rows]

    async def append(self, user_id: int, turns: list[dict]) -> None:
        await self.conn.executemany(
            "INSERT INTO dialog (user_id, role, content) VALUES (?, ?, ?)",
            [(user_id, t["role"], json.dumps(t["content"], ensure_ascii=False)) for t in turns],
        )
        await self.conn.commit()

    async def forget_everything(self, user_id: int) -> None:
        for table in ("dialog", "notes", "planned"):
            await self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await self.conn.execute("UPDATE users SET system = NULL WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    # notes

    async def notes(self, user_id: int) -> list[tuple[int, str]]:
        rows = await self._all(
            "SELECT id, text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, MAX_NOTES)
        )
        return rows[::-1]

    async def add_note(self, user_id: int, text: str) -> int:
        return (await self._exec("INSERT INTO notes (user_id, text) VALUES (?, ?)", (user_id, text))).lastrowid

    async def update_note(self, user_id: int, nid: int, text: str) -> bool:
        cur = await self._exec("UPDATE notes SET text = ? WHERE id = ? AND user_id = ?", (text, nid, user_id))
        return cur.rowcount > 0

    async def delete_note(self, user_id: int, nid: int) -> bool:
        cur = await self._exec("DELETE FROM notes WHERE id = ? AND user_id = ?", (nid, user_id))
        return cur.rowcount > 0

    # planned messages

    async def plan(self, user_id: int, due: int, reason: str) -> int:
        cur = await self._exec("INSERT INTO planned (user_id, due, reason) VALUES (?, ?, ?)", (user_id, due, reason))
        return cur.lastrowid

    async def planned(self, user_id: int) -> list[tuple[int, int, str]]:
        return await self._all(
            "SELECT id, due, reason FROM planned WHERE user_id = ? AND done = 0 ORDER BY due", (user_id,)
        )

    async def cancel_plan(self, user_id: int, pid: int) -> bool:
        cur = await self._exec(
            "UPDATE planned SET done = 1 WHERE id = ? AND user_id = ? AND done = 0", (pid, user_id)
        )
        return cur.rowcount > 0

    async def due_plans(self, now: int) -> list[tuple[int, int, str]]:
        return await self._all(
            "SELECT id, user_id, reason FROM planned WHERE done = 0 AND due <= ? ORDER BY due", (now,)
        )

    async def finish_plan(self, pid: int) -> None:
        await self._exec("UPDATE planned SET done = 1 WHERE id = ?", (pid,))

    # payments

    async def record_payment(self, charge_id: str, user_id: int, amount: int, recurring: bool) -> bool:
        cur = await self._exec(
            "INSERT OR IGNORE INTO payments (charge_id, user_id, amount, recurring) VALUES (?, ?, ?, ?)",
            (charge_id, user_id, amount, int(recurring)),
        )
        return cur.rowcount > 0

    async def extend(self, user_id: int, seconds: int, now: int) -> int:
        """Add paid time on top of whatever is left; returns the new paid_until."""
        user = await self.user(user_id)
        until = max(user.paid_until, now) + seconds
        await self.update(user_id, paid_until=until)
        return until

    # YooKassa

    async def add_yk_payment(self, pid: str, user_id: int, amount: str, status: str, renewal: bool) -> None:
        await self._exec(
            "INSERT OR IGNORE INTO yk_payments (id, user_id, amount, status, renewal) VALUES (?, ?, ?, ?, ?)",
            (pid, user_id, amount, status, int(renewal)),
        )

    async def pending_yk(self, user_id: int | None = None) -> list[tuple[str, int, int, int]]:
        sql = "SELECT id, user_id, renewal, created_at FROM yk_payments WHERE status IN ('pending', 'waiting_for_capture')"
        if user_id is None:
            return await self._all(sql)
        return await self._all(sql + " AND user_id = ?", (user_id,))

    async def settle_yk(self, pid: str, status: str) -> bool:
        """Move a pending payment to its final status; False if it was already settled."""
        cur = await self._exec(
            "UPDATE yk_payments SET status = ? WHERE id = ? AND status IN ('pending', 'waiting_for_capture')",
            (status, pid),
        )
        return cur.rowcount > 0

    async def renewal_candidates(self, now: int, ahead: int) -> list[User]:
        cur = await self.conn.execute(
            "SELECT * FROM users WHERE autopay = 1 AND pay_method_id IS NOT NULL "
            "AND paid_until < ? AND paid_until > ? AND NOT EXISTS ("
            "  SELECT 1 FROM yk_payments p WHERE p.user_id = users.user_id AND p.renewal = 1 "
            "  AND (p.status IN ('pending', 'waiting_for_capture') OR p.created_at > ?))",
            (now + ahead, now - 3 * 86400, now - 86400),
        )
        return [User(*row) for row in await cur.fetchall()]

    # spend limits

    async def add_spend(self, user_id: int, day: str, usd: float) -> None:
        await self._exec(
            "INSERT INTO spend (user_id, day, usd) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET usd = usd + excluded.usd",
            (user_id, day, usd),
        )

    async def spent(self, user_id: int, since_day: str) -> float:
        (usd,), = await self._all(
            "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE user_id = ? AND day >= ?", (user_id, since_day)
        )
        return usd

    async def admin_stats(self, now: int) -> dict:
        (users,), = await self._all("SELECT COUNT(*) FROM users")
        (active,), = await self._all("SELECT COUNT(*) FROM users WHERE last_user_at > ?", (now - 7 * 86400,))
        (subs,), = await self._all("SELECT COUNT(*) FROM users WHERE paid_until > ?", (now,))
        (stars,), = await self._all("SELECT COALESCE(SUM(amount), 0) FROM payments")
        (month,), = await self._all(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE paid_at > ?", (now - 30 * 86400,)
        )
        (inp, out, cached), = await self._all(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cached_tokens),0) FROM users"
        )
        (rub_month,), = await self._all(
            "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM yk_payments "
            "WHERE status = 'succeeded' AND created_at > ?", (now - 30 * 86400,)
        )
        return dict(users=users, active=active, subs=subs, stars=stars, month=month, rub_month=rub_month,
                    inp=inp, out=out, cached=cached)
