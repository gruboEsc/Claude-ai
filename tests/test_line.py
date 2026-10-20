import asyncio
import base64
import hashlib
import hmac
import json
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot.line as line
from bot.brain import Companion
from bot.config import Config
from bot.db import Storage
from bot.line import LineBot, LineChannel, web_app
from bot.tools import Tools
from tests.test_companion import FakeClaude

SECRET = "line-secret"
CFG = Config(telegram_token="x", anthropic_api_key="k", web_search=False, free_messages=1,
             line_secret=SECRET, line_token="tok")
PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeLine:
    """Stands in for api.line.me and api-data.line.me."""

    def __init__(self):
        self.calls = []

    def app(self) -> web.Application:
        async def record(request):
            self.calls.append((request.path, await request.json()))
            return web.json_response({})

        async def profile(request):
            return web.json_response({"userId": request.match_info["uid"], "language": "ja"})

        async def content(request):
            return web.Response(body=PNG, content_type="image/png")

        app = web.Application()
        app.router.add_post("/v2/bot/message/reply", record)
        app.router.add_post("/v2/bot/message/push", record)
        app.router.add_post("/v2/bot/chat/loading/start", record)
        app.router.add_get("/v2/bot/profile/{uid}", profile)
        app.router.add_get("/v2/bot/message/{mid}/content", content)
        return app

    def sent(self, kind):
        return [body for path, body in self.calls if path.endswith(f"/message/{kind}")]


def _event(kind, user="Uabc", token="rt1", **extra):
    return {"type": kind, "replyToken": token, "source": {"type": "user", "userId": user}, **extra}


def _signed(events):
    body = json.dumps({"events": events}).encode()
    sig = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return body, {"X-Line-Signature": sig, "Content-Type": "application/json"}


def scenario(tmp_path, steps):
    async def run():
        fake = FakeLine()
        api = TestServer(fake.app())
        await api.start_server()
        base = str(api.make_url("")).rstrip("/")
        line.API, line.DATA_API = base + "/v2/bot", base + "/v2/bot"

        storage = Storage(str(tmp_path / "bot.db"))
        await storage.open()
        channel = LineChannel(SECRET, "tok")
        claude = FakeClaude("こんにちは！\n\n元気？")
        companion = Companion(CFG, storage, claude, Tools(storage, CFG.timezone), [channel])
        bot = LineBot(CFG, companion, channel)
        client = TestClient(TestServer(web_app(bot)))
        await client.start_server()

        async def post(events):
            body, headers = _signed(events)
            resp = await client.post("/line/webhook", data=body, headers=headers)
            assert resp.status == 200
            await asyncio.gather(*bot.tasks)

        try:
            await steps(post, fake, storage, claude, client, bot)
        finally:
            await client.close()
            await channel.close()
            await api.close()
            await storage.close()

    asyncio.run(run())


def test_bad_signature_is_rejected(tmp_path):
    async def steps(post, fake, storage, claude, client, bot):
        resp = await client.post("/line/webhook", data=b'{"events": []}', headers={"X-Line-Signature": "nope"})
        assert resp.status == 401

    scenario(tmp_path, steps)


def test_follow_sends_intro_and_greeting_in_one_free_reply(tmp_path):
    async def steps(post, fake, storage, claude, client, bot):
        await post([_event("follow")])
        [reply] = fake.sent("reply")
        texts = [m["text"] for m in reply["messages"]]
        assert texts[0].startswith("こんにちは！ここでは") and "ユイ" in texts[0]  # Japanese intro, local name
        assert texts[1:] == ["こんにちは！", "元気？"]
        assert reply["replyToken"] == "rt1" and fake.sent("push") == []
        uid = await storage.account("line", "Uabc")
        user = await storage.user(uid)
        assert uid < 0 and user.platform == "line" and user.lang == "ja"
        assert "Write in Japanese" in claude.calls[0][0]

    scenario(tmp_path, steps)


def test_messages_trial_paywall_and_photo(tmp_path):
    async def steps(post, fake, storage, claude, client, bot):
        image = {"type": "image", "id": "m1"}
        await post([_event("message", token="rt2", message=image)])
        history = claude.calls[-1][1][-1]["content"]
        assert history[1]["type"] == "image" and history[1]["source"]["media_type"] == "image/png"
        assert fake.sent("reply")[-1]["replyToken"] == "rt2"

        # free_messages=1, so the next message hits the paywall instead of Claude
        calls = len(claude.calls)
        await post([_event("message", token="rt3", message={"type": "text", "text": "まだ話したい"})])
        assert len(claude.calls) == calls
        assert "サブスクリプション" in fake.sent("reply")[-1]["messages"][0]["text"]

    scenario(tmp_path, steps)


def test_messages_without_a_fresh_reply_token_are_pushed(tmp_path):
    async def steps(post, fake, storage, claude, client, bot):
        await post([_event("follow", token="rt1")])  # uses the reply token
        uid = await storage.account("line", "Uabc")
        await storage.update(uid, paid_until=int(time.time()) + 86400)
        await storage.plan(uid, int(time.time()) - 1, "ask about the interview")
        await bot.companion.tick()  # the planned message has no event, hence no reply token
        [push] = fake.sent("push")
        assert push["to"] == "Uabc" and [m["text"] for m in push["messages"]] == ["こんにちは！", "元気？"]
        assert "interview" in claude.calls[-1][1][-1]["content"][1]["text"]

    scenario(tmp_path, steps)


def test_commands_and_unfollow(tmp_path):
    async def steps(post, fake, storage, claude, client, bot):
        await post([_event("message", token="c1", message={"type": "text", "text": "/forget"})])
        quick = fake.sent("reply")[-1]["messages"][-1]["quickReply"]["items"]
        assert [i["action"]["text"] for i in quick] == ["/forget yes", "/forget no"]

        await post([_event("message", token="c2", message={"type": "text", "text": "/settings off"})])
        uid = await storage.account("line", "Uabc")
        assert (await storage.user(uid)).checkins == 0

        await post([_event("message", token="c3", message={"type": "text", "text": "/language en"})])
        assert (await storage.user(uid)).lang == "en"
        assert fake.sent("reply")[-1]["messages"][0]["text"] == "Done — English it is 🙂"

        await post([_event("unfollow", token=None)])
        assert (await storage.user(uid)).blocked == 1
        assert not claude.calls  # commands never reach Claude

    scenario(tmp_path, steps)
