import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.brain import Companion, bubbles
from bot.claude import Claude, Reply, cost
from bot.companion import build_system, stamp
from bot.config import Config
from bot.db import Storage
from bot.tools import TOOLS, Tools

CFG = Config(telegram_token="x", anthropic_api_key="k", web_search=False, free_messages=2,
             checkin_from_hour=0, checkin_to_hour=24)


def run(coro):
    return asyncio.run(coro)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **_):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, *_):
        pass


class FakeClaude:
    def __init__(self, text="Привет!\n\nКак ты?"):
        self.text = text
        self.calls = []

    async def reply(self, system, history, effort, run_tool, on_text=None):
        self.calls.append((system, history))
        await run_tool("remember", {"fact": "любит котов"})
        return Reply(text=self.text, turns=[{"role": "assistant", "content": [{"type": "text", "text": self.text}]}])


async def _setup(tmp_path, claude=None):
    storage = Storage(str(tmp_path / "bot.db"))
    await storage.open()
    companion = Companion(CFG, storage, claude or FakeClaude(), Tools(storage, CFG.timezone))
    return storage, companion


def test_conversation_is_saved_and_split_into_bubbles(tmp_path):
    async def scenario():
        storage, companion = await _setup(tmp_path)
        bot = FakeBot()
        assert await companion.converse(bot, 7, [{"type": "text", "text": "привет"}])
        assert bot.sent == [(7, "Привет!"), (7, "Как ты?")]
        history = await storage.history(7)
        assert [t["role"] for t in history] == ["user", "assistant"]
        assert history[0]["content"][0]["text"].startswith("[")  # time stamp
        assert await storage.notes(7) == [(1, "любит котов")]
        # the system prompt is frozen for the dialog: the new note is not in it yet
        system_before = companion.claude.calls[0][0]
        await companion.converse(bot, 7, [{"type": "text", "text": "ещё"}])
        assert companion.claude.calls[1][0] == system_before
        assert companion.claude.calls[1][1][:2] == history  # append-only
        await storage.close()

    run(scenario())


def test_trial_and_subscription(tmp_path):
    async def scenario():
        storage, companion = await _setup(tmp_path)
        user = await storage.user(1)
        assert companion.has_access(user) and not companion.subscribed(user)
        for _ in range(2):
            await storage.user_wrote(1, int(time.time()), counted=True)
        assert not companion.has_access(await storage.user(1))
        await storage.update(1, paid_until=int(time.time()) + 3600)
        user = await storage.user(1)
        assert companion.has_access(user) and companion.subscribed(user)
        assert await storage.record_payment("c1", 1, 499, False)
        assert not await storage.record_payment("c1", 1, 499, False)  # duplicate
        await storage.close()

    run(scenario())


def test_checkin_only_for_idle_subscribers_and_capped(tmp_path):
    async def scenario():
        storage, companion = await _setup(tmp_path)
        bot = FakeBot()
        now = int(time.time())
        long_ago = now - 30 * 3600
        await storage.user(1)
        await storage.update(1, paid_until=now + 86400, last_user_at=long_ago)  # idle subscriber
        await storage.user(2)
        await storage.update(2, last_user_at=long_ago)  # idle, not subscribed
        await storage.user(3)
        await storage.update(3, paid_until=now + 86400, last_user_at=now - 60)  # active

        await companion.tick(bot)
        assert [uid for uid, _ in bot.sent] == [1, 1]
        note = companion.claude.calls[0][1][-1]["content"][1]["text"]
        assert note.startswith("[Service note") and "30 h" in note
        # just wrote, so no second check-in right away
        bot.sent.clear()
        await companion.tick(bot)
        assert bot.sent == []
        # after two unanswered check-ins it stops
        await storage.update(1, last_bot_at=long_ago, unanswered=2)
        await companion.tick(bot)
        assert bot.sent == []
        await storage.close()

    run(scenario())


def test_planned_message_is_delivered_once(tmp_path):
    async def scenario():
        storage, companion = await _setup(tmp_path)
        bot = FakeBot()
        await storage.user(5)
        await storage.plan(5, int(time.time()) - 1, "спросить, как прошло собеседование")
        await companion.tick(bot)
        await companion.tick(bot)
        assert len(companion.claude.calls) == 1
        assert "собеседование" in companion.claude.calls[0][1][-1]["content"][1]["text"]
        await storage.close()

    run(scenario())


def test_tools(tmp_path):
    async def scenario():
        storage, companion = await _setup(tmp_path)
        tools = companion.tools
        await storage.user(9)
        assert "Time zone set" in await tools.run(9, "set_timezone", {"tz": "Asia/Novosibirsk"})
        assert str(await tools.tz(9)) == "Asia/Novosibirsk"
        with pytest.raises(ValueError):
            await tools.run(9, "set_timezone", {"tz": "Марс"})
        when = (datetime.now(ZoneInfo("Asia/Novosibirsk")) + timedelta(days=1)).replace(tzinfo=None)
        out = await tools.run(9, "schedule_message", {"when": when.isoformat(timespec="minutes"), "reason": "удачи"})
        assert out.startswith("Planned #1")
        assert "удачи" in await tools.run(9, "list_planned", {})
        assert await tools.run(9, "cancel_planned", {"id": "#1"}) == "Cancelled."
        with pytest.raises(ValueError, match="past"):
            await tools.run(9, "schedule_message", {"when": "2000-01-01T10:00", "reason": "x"})
        nid = int((await tools.run(9, "remember", {"fact": "зовут Петя"})).split("#")[1].rstrip("."))
        assert await tools.run(9, "update_note", {"id": nid, "fact": "зовут Пётр"}) == "Updated."
        assert await storage.notes(9) == [(nid, "зовут Пётр")]
        assert await tools.run(9, "delete_note", {"id": nid}) == "Deleted."
        await storage.forget_everything(9)
        await storage.close()

    run(scenario())


def test_bubbles():
    assert bubbles("**Привет**\n\nкак дела?") == ["Привет", "как дела?"]
    assert len(bubbles("\n\n".join("abcdef"))) == 4
    long = bubbles("a" * 5000)
    assert all(len(p) <= 4096 for p in long) and "".join(long) == "a" * 5000


def test_system_prompt():
    system = build_system("Аня", "f", "ru", [(3, "зовут Петя")], "Europe/Moscow")
    assert "You are Аня" in system and "#3: зовут Петя" in system and "Write in Russian" in system
    assert "8-800-2000-122" in system and "109" in system and "findahelpline.com" in system
    ja = build_system("レン", "m", "ja", [], "Asia/Tokyo")
    assert "Write in Japanese" in ja and "です/ます" in ja and "getting to know each other" in ja
    assert stamp(datetime(2026, 10, 20, 15, 40)) == "[2026-10-20, Tuesday, 15:40]"


def test_tool_definitions_are_valid():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    for tool in TOOLS:
        assert set(tool["input_schema"]["required"]) <= set(tool["input_schema"]["properties"])


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
        return dict(vars(self))


def _message(stop_reason, *blocks):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                            cache_read_input_tokens=3)
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks), usage=usage)


def test_claude_tool_loop_and_compaction_flag():
    replies = [
        _message("tool_use", Block(type="tool_use", id="t1", name="remember", input={"fact": "x"})),
        _message("end_turn", Block(type="compaction", content="..."), Block(type="text", text="Ага!")),
    ]
    sent = []

    def stream(**kwargs):
        sent.append(kwargs)
        return FakeStream(replies.pop(0))

    claude = Claude(CFG, TOOLS, "keep facts")
    claude.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))

    async def run_tool(name, args):
        return "ok"

    reply = run(claude.reply("sys", [{"role": "user", "content": "hi"}], "low", run_tool))
    assert reply.text == "Ага!" and reply.compacted
    assert [t["role"] for t in reply.turns] == ["assistant", "user", "assistant"]
    assert sent[0]["context_management"] == {"edits": [{
        "type": "compact_20260112",
        "trigger": {"type": "input_tokens", "value": 50000},
        "instructions": "keep facts",
    }]}
    assert sent[1]["messages"][: len(sent[0]["messages"])] == sent[0]["messages"]


def test_cost():
    assert cost("claude-opus-5-5", 1_000_000, 1_000_000, 1_000_000) == pytest.approx(24.2)


def test_compaction_steps_are_billed():
    step = lambda i, o: SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0,
                                        cache_read_input_tokens=0)
    message = _message("end_turn", Block(type="text", text="ok"))
    message.usage.iterations = [step(40000, 2000), step(3000, 100)]  # compaction + the reply

    def stream(**kwargs):
        return FakeStream(message)

    claude = Claude(CFG, TOOLS)
    claude.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))

    async def run_tool(name, args):
        return "ok"

    reply = run(claude.reply("sys", [{"role": "user", "content": "hi"}], "low", run_tool))
    assert (reply.input_tokens, reply.output_tokens) == (43000, 2100)


def test_history_starts_at_latest_compaction(tmp_path):
    async def scenario():
        storage = Storage(str(tmp_path / "bot.db"))
        await storage.open()
        turn = lambda role, text: {"role": role, "content": [{"type": "text", "text": text}]}
        await storage.append(1, [turn("user", "old q"), turn("assistant", "old a")])
        assert len(await storage.history(1)) == 2
        compacted = {"role": "assistant", "content": [{"type": "compaction", "content": "сводка"},
                                                      {"type": "text", "text": "a2"}]}
        await storage.append(1, [turn("user", "q2"), compacted, turn("user", "q3")])
        history = await storage.history(1)
        assert [t["content"][-1]["text"] for t in history] == ["q2", "a2", "q3"]
        assert history[0]["role"] == "user"
        await storage.append(2, [turn("user", "other")])  # other people are unaffected
        assert len(await storage.history(2)) == 1
        await storage.close()

    run(scenario())
