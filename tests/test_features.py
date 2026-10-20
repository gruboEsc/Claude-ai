import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.claude import Claude, cost
from bot.config import Config
from bot.db import Storage
from bot.handlers import _transcript
from bot.reminders import TOOLS, Reminders

TZ = ZoneInfo("Europe/Moscow")


def run(coro):
    return asyncio.run(coro)


async def _storage(tmp_path) -> Storage:
    storage = Storage(str(tmp_path / "bot.db"))
    await storage.open()
    return storage


def test_reminder_lifecycle(tmp_path):
    async def scenario():
        storage = await _storage(tmp_path)
        r = Reminders(storage, TZ)
        when = (r.now() + timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="minutes")
        assert "set for" in await r.run_tool(42, "set_reminder", {"when": when, "text": "позвонить маме"})
        listing = await r.run_tool(42, "list_reminders", {})
        assert "позвонить маме" in listing
        assert await storage.due_reminders(int(r.now().timestamp())) == []
        later = int((r.now() + timedelta(hours=2)).timestamp())
        [(rid, chat_id, text)] = await storage.due_reminders(later)
        assert (chat_id, text) == (42, "позвонить маме")
        assert "cancelled" in await r.run_tool(42, "cancel_reminder", {"id": rid})
        assert await storage.due_reminders(later) == []
        await storage.close()

    run(scenario())


def test_reminder_rejects_bad_input(tmp_path):
    async def scenario():
        storage = await _storage(tmp_path)
        r = Reminders(storage, TZ)
        with pytest.raises(ValueError, match="past"):
            await r.run_tool(1, "set_reminder", {"when": "2000-01-01T00:00", "text": "x"})
        with pytest.raises(ValueError, match="ISO"):
            await r.run_tool(1, "set_reminder", {"when": "завтра", "text": "x"})
        with pytest.raises(ValueError):
            await r.run_tool(1, "cancel_reminder", {"id": "1"})
        await storage.close()

    run(scenario())


class FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for block in self.message.content:
            if block.type == "text":
                yield block.text

    async def get_final_message(self):
        return self.message


class Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items()}


def _message(stop_reason, *blocks):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                            cache_read_input_tokens=3, server_tool_use=None)
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks), usage=usage)


def test_tool_round_is_executed_and_recorded():
    replies = [
        _message("tool_use", Block(type="text", text="Ставлю."),
                 Block(type="tool_use", id="t1", name="list_reminders", input={})),
        _message("end_turn", Block(type="text", text="Готово!")),
    ]
    sent = []

    def stream(**kwargs):
        sent.append(kwargs["messages"])
        return FakeStream(replies.pop(0))

    claude = Claude(Config(telegram_token="x", anthropic_api_key="k", web_search=False), TOOLS)
    claude.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    calls = []

    async def run_tool(name, args):
        calls.append(name)
        return "No pending reminders."

    async def on_text(_):
        pass

    history = [{"role": "user", "content": [{"type": "text", "text": "что у меня?"}]}]
    reply = run(claude.reply("sys", history, "low", on_text, run_tool))

    assert calls == ["list_reminders"]
    assert reply.text == "Ставлю.\n\nГотово!"
    assert [t["role"] for t in reply.turns] == ["assistant", "user", "assistant"]
    assert reply.turns[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "No pending reminders."}
    ]
    # the second request replays the first exactly and only appends
    assert sent[1][: len(sent[0])] == sent[0]
    assert (reply.input_tokens, reply.output_tokens, reply.cached_tokens) == (20, 10, 6)


def test_transcript_skips_plumbing():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "[2026-10-20 15:00, Tuesday (Europe/Moscow)]"},
                                     {"type": "image", "source": {}}, {"type": "text", "text": "что это?"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Кот."}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]},
    ]
    md = _transcript(history)
    assert "2026-10-20" not in md and "что это?" in md and "Кот." in md and "вложений: 1" in md
    assert md.count("###") == 2


def test_cost():
    assert cost("claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000) == pytest.approx(24.2)
    assert cost("unknown-model", 1, 1, 1) is None
