"""LINE Messaging API: the channel and the webhook (https://developers.line.biz/en/reference/messaging-api/).

Replies go out with the event's reply token, which is free; anything sent later (check-ins,
planned messages, payment notices) is a push message and counts toward the plan's quota.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time

import aiohttp
from aiohttp import web

from .brain import Companion
from .channels import Channel
from .config import Config
from .i18n import LANGS, lang_of, t

log = logging.getLogger(__name__)

API = "https://api.line.me/v2/bot"
DATA_API = "https://api-data.line.me/v2/bot"
TOKEN_TTL = 50  # seconds a reply token is trusted; LINE invalidates them shortly after the event
MAX_MESSAGES = 5  # per reply / push call
TEXT_LIMIT = 5000
IMAGE_LIMIT = 10 * 1024 * 1024


class LineChannel(Channel):
    name = "line"

    def __init__(self, secret: str, token: str):
        self.secret = secret.encode()
        self.headers = {"Authorization": f"Bearer {token}"}
        self.session: aiohttp.ClientSession | None = None
        self.reply_tokens: dict[str, tuple[str, float]] = {}

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers=self.headers, trust_env=True, timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    def verify(self, body: bytes, signature: str) -> bool:
        digest = hmac.new(self.secret, body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")

    def remember_reply_token(self, user_id: str, token: str) -> None:
        self.reply_tokens[user_id] = (token, time.monotonic())

    def _take_reply_token(self, user_id: str) -> str | None:
        token, at = self.reply_tokens.pop(user_id, (None, 0.0))
        return token if token and time.monotonic() - at < TOKEN_TTL else None

    async def _post(self, url: str, body: dict) -> int:
        async with self._session().post(url, json=body) as resp:
            if resp.status >= 400:
                log.warning("LINE %s -> %s: %s", url.rsplit("/", 1)[-1], resp.status, await resp.text())
            return resp.status

    async def send(self, external_id: str, parts: list[str], quick: list[tuple[str, str]] | None = None) -> bool:
        messages = [{"type": "text", "text": p[:TEXT_LIMIT]} for p in parts if p.strip()]
        if quick and messages:
            messages[-1]["quickReply"] = {"items": [
                {"type": "action", "action": {"type": "message", "label": label[:20], "text": text}}
                for label, text in quick[:13]
            ]}
        for i in range(0, len(messages), MAX_MESSAGES):
            batch = messages[i:i + MAX_MESSAGES]
            token = self._take_reply_token(external_id)
            if token:
                status = await self._post(f"{API}/message/reply", {"replyToken": token, "messages": batch})
                if status < 400:
                    continue
            # No usable reply token (or it expired): fall back to a push message.
            status = await self._post(f"{API}/message/push", {"to": external_id, "messages": batch})
            if status >= 400:
                # Unfollowed users are marked blocked by the unfollow event; a failed push is just logged.
                return status not in (400, 403)
        return True

    async def typing(self, external_id: str) -> None:
        await self._post(f"{API}/chat/loading/start", {"chatId": external_id, "loadingSeconds": 20})

    async def profile_language(self, external_id: str) -> str | None:
        async with self._session().get(f"{API}/profile/{external_id}") as resp:
            if resp.status >= 400:
                return None
            return (await resp.json()).get("language")

    async def content(self, message_id: str) -> tuple[bytes, str]:
        async with self._session().get(f"{DATA_API}/message/{message_id}/content") as resp:
            resp.raise_for_status()
            data = await resp.read()
            return data, resp.headers.get("Content-Type", "image/jpeg").split(";")[0]


class LineBot:
    """Turns LINE webhook events into companion turns."""

    def __init__(self, cfg: Config, companion: Companion, channel: LineChannel):
        self.cfg = cfg
        self.companion = companion
        self.storage = companion.storage
        self.channel = channel
        self.tasks: set[asyncio.Task] = set()

    async def webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not self.channel.verify(body, request.headers.get("X-Line-Signature", "")):
            return web.Response(status=401)
        for event in json.loads(body).get("events", []):
            # Answer LINE right away; the companion's reply can take a while.
            task = asyncio.create_task(self._safe(event))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return web.Response(text="ok")

    async def _safe(self, event: dict) -> None:
        try:
            await self.handle(event)
        except Exception:
            log.exception("LINE event failed: %s", event.get("type"))

    async def handle(self, event: dict) -> None:
        source = event.get("source") or {}
        if source.get("type") != "user":
            return  # groups and rooms are not supported
        external = source["userId"]
        uid = await self.storage.account("line", external)
        if event.get("replyToken"):
            self.channel.remember_reply_token(external, event["replyToken"])
        user = await self.storage.user(uid)
        if not user.lang:
            await self.storage.update(uid, lang=lang_of(await self.channel.profile_language(external) or "ja"))
            user = await self.storage.user(uid)
        lang = self.companion.lang(user)

        kind = event.get("type")
        if kind == "unfollow":
            await self.storage.update(uid, blocked=1)
            return
        if kind == "follow":
            await self.storage.update(uid, blocked=0)
            if not user.system:
                await self.companion.converse(
                    uid,
                    [{"type": "text", "text": "[Service note: this is the very start of your acquaintance — the "
                      "person has just added you. Say hello, briefly introduce yourself in your own way and ask "
                      "how to address them.]"}],
                    proactive=True,
                    prefix=[t(lang, "intro", name=self.companion.name(user), free=self.cfg.free_messages)],
                )
            return
        if kind != "message":
            return

        message = event["message"]
        if message["type"] == "text" and message["text"].startswith("/"):
            await self.command(user, lang, message["text"].strip())
            return
        if self.cfg.allowed_users and uid not in self.cfg.admin_ids:
            await self.channel.send(external, [t(lang, "closed")])
            return
        blocked = await self.companion.admit(user)
        if blocked:
            key, fields = blocked
            await self.channel.send(external, [t(lang, "line_paywall" if key == "paywall" else key, **fields)])
            return

        content = await self._content(message, lang, external)
        if content is None:
            return
        await self.storage.user_wrote(uid, int(time.time()), counted=not self.companion.subscribed(user))
        await self.companion.converse(uid, content)

    async def _content(self, message: dict, lang: str, external: str) -> list[dict] | None:
        kind = message["type"]
        if kind == "text":
            return [{"type": "text", "text": message["text"]}]
        if kind == "image":
            data, mime = await self.channel.content(message["id"])
            if len(data) > IMAGE_LIMIT or mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                await self.channel.send(external, [t(lang, "bad_file")])
                return None
            return [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.standard_b64encode(data).decode()}},
                {"type": "text", "text": "[sent a photo without a caption]"},
            ]
        if kind == "sticker":
            words = ", ".join(message.get("keywords", [])[:5])
            return [{"type": "text", "text": f"[sent a sticker{': ' + words if words else ''}]"}]
        if kind in ("audio", "video"):
            await self.channel.send(external, [t(lang, "voice")])
            return None
        await self.channel.send(external, [t(lang, "bad_file")])
        return None

    async def command(self, user, lang: str, text: str) -> None:
        """Text commands, since LINE has no bot commands: /help, /status, /settings, /language, /forget."""
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip().lower()
        ext, uid = user.external_id, user.user_id
        name = self.companion.name(user)

        if cmd == "/forget":
            if arg == "yes":
                async with self.companion.locks[uid]:
                    await self.storage.forget_everything(uid)
                await self.channel.send(ext, [t(lang, "forgot")])
            elif arg == "no":
                await self.channel.send(ext, [t(lang, "kept")])
            else:
                await self.channel.send(ext, [t(lang, "forget_confirm")], quick=[
                    (t(lang, "btn_yes_erase"), "/forget yes"), (t(lang, "btn_no"), "/forget no")])
        elif cmd == "/settings":
            if arg in ("on", "off"):
                await self.storage.update(uid, checkins=int(arg == "on"))
                await self.channel.send(ext, [t(lang, "toggled_on" if arg == "on" else "toggled_off")])
            else:
                await self.channel.send(
                    ext,
                    [t(lang, "settings", name=name, from_h=self.cfg.checkin_from_hour, to_h=self.cfg.checkin_to_hour)],
                    quick=[(t(lang, "btn_checkins_on"), "/settings on"), (t(lang, "btn_checkins_off"), "/settings off")],
                )
        elif cmd == "/language":
            if arg in LANGS:
                async with self.companion.locks[uid]:
                    await self.storage.update(uid, lang=arg, system=None)
                await self.channel.send(ext, [t(arg, "language_set")])
            else:
                await self.channel.send(ext, [t(lang, "language_pick")],
                                        quick=[(info.native, f"/language {code}") for code, info in LANGS.items()])
        elif cmd == "/status":
            if self.companion.subscribed(user):
                await self.channel.send(ext, [t(lang, "status_active", date=time.strftime(
                    "%d.%m.%Y", time.localtime(user.paid_until)))])
            else:
                left = max(self.cfg.free_messages - user.used, 0)
                await self.channel.send(ext, [t(lang, "line_trial", left=left, free=self.cfg.free_messages)])
        else:
            await self.channel.send(ext, [t(lang, "line_help", name=name)])


async def _health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


def web_app(line: LineBot | None) -> web.Application:
    app = web.Application()
    app.router.add_get("/", _health)
    if line:
        app.router.add_post("/line/webhook", line.webhook)
    return app
