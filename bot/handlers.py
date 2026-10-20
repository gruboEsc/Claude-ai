import asyncio
import base64
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import anthropic
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)

from .claude import Claude, cost
from .config import EFFORTS, Config
from .db import Storage
from .formatting import preview, split_markdown, to_html
from .personas import PERSONAS, get_persona
from .reminders import Reminders

log = logging.getLogger(__name__)

EDIT_INTERVAL = 1.2  # seconds between streaming edits; Telegram throttles faster edits
ALBUM_WAIT = 1.0  # seconds to collect the rest of a media group
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit
MAX_QUOTE = 4000
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TEXT_SUFFIXES = (
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".yaml", ".yml", ".toml", ".html",
    ".css", ".xml", ".sql", ".sh", ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".h", ".log", ".ini",
)
EFFORT_TITLES = {
    "low": "⚡ Быстро",
    "medium": "⚖️ Сбалансированно",
    "high": "🧠 Вдумчиво",
    "xhigh": "🔬 Очень глубоко",
    "max": "🚀 Максимум",
}
ACTIONS = {
    "again": ("🔄 Другой вариант", "Дай другой вариант ответа на мой предыдущий вопрос."),
    "more": ("➕ Подробнее", "Расскажи подробнее, с примерами."),
    "short": ("✂️ Короче", "Перескажи свой последний ответ в 2–3 предложениях."),
}

HELP = """\
<b>Привет! Я бот на Claude 🤖</b>

Просто пиши — я помню весь диалог.
• 📷 Фото и альбомы — опишу, прочитаю текст, решу задачу с картинки
• 📄 PDF и файлы с кодом/текстом — разберу и отвечу по ним
• 🌐 Сам ищу в интернете, когда нужны свежие данные
• ⏰ «Напомни завтра в 9 позвонить маме» — напомню
• ↩️ Ответь на любое сообщение и спроси про него — учту, о чём речь
• Под ответами кнопки: другой вариант, подробнее, короче. ⏹ — остановить ответ

<b>Команды</b>
/new — начать новый диалог
/mode — режим (программист, переводчик, копирайтер…)
/effort — насколько глубоко думать
/reminders — мои напоминания
/export — скачать диалог файлом
/stats — статистика и расходы
/help — эта справка

В группах отвечаю, когда меня упоминают или отвечают на моё сообщение."""


def setup(cfg: Config, storage: Storage, claude: Claude, reminders: Reminders) -> Router:
    router = Router()
    locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
    running: dict[int, asyncio.Task] = {}  # chat_id -> reply in progress, for the stop button
    albums: dict[str, list[Message]] = {}

    def allowed(user_id: int | None) -> bool:
        return not cfg.allowed_users or user_id in cfg.allowed_users

    def user_ok(message: Message) -> bool:
        return bool(message.from_user) and allowed(message.from_user.id)

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if not user_ok(message):
            await message.answer(f"⛔ Доступ закрыт. Твой id: <code>{message.from_user.id}</code>")
            return
        await message.answer(HELP)

    @router.message(Command("new", "reset"))
    async def cmd_new(message: Message) -> None:
        if not user_ok(message):
            return
        await storage.reset(message.chat.id)
        await message.answer("🧹 Начали с чистого листа. О чём поговорим?")

    @router.message(Command("mode"))
    async def cmd_mode(message: Message) -> None:
        if not user_ok(message):
            return
        current = get_persona((await storage.settings(message.chat.id)).persona)
        await message.answer(
            f"Сейчас: <b>{current.title}</b>\nВыбери режим (диалог начнётся заново):",
            reply_markup=_choices({k: p.title for k, p in PERSONAS.items()}, "mode", current.key),
        )

    @router.message(Command("effort"))
    async def cmd_effort(message: Message) -> None:
        if not user_ok(message):
            return
        current = (await storage.settings(message.chat.id)).effort or cfg.default_effort
        await message.answer(
            "Насколько глубоко думать над ответами?\nБыстрее — дешевле, глубже — умнее.",
            reply_markup=_choices({e: EFFORT_TITLES[e] for e in EFFORTS}, "effort", current),
        )

    @router.message(Command("stats"))
    async def cmd_stats(message: Message) -> None:
        if not user_ok(message):
            return
        s = await storage.settings(message.chat.id)
        spent = cost(cfg.model, s.input_tokens, s.output_tokens, s.cached_tokens)
        await message.answer(
            "📊 <b>Статистика чата</b>\n"
            f"Режим: {get_persona(s.persona).title}\n"
            f"Глубина: {EFFORT_TITLES[s.effort or cfg.default_effort]}\n"
            f"Сообщений в текущем диалоге: {s.turns}\n"
            f"Запросов к Claude всего: {s.requests}\n"
            f"Токенов: вход {s.input_tokens:,} · из кэша {s.cached_tokens:,} · выход {s.output_tokens:,}\n"
            + (f"Потрачено: ≈ ${spent:.2f}\n" if spent is not None else "")
            + f"Модель: <code>{cfg.model}</code>"
        )

    @router.message(Command("reminders"))
    async def cmd_reminders(message: Message) -> None:
        if not user_ok(message):
            return
        rows = await storage.pending_reminders(message.chat.id)
        if not rows:
            await message.answer("⏰ Напоминаний нет. Просто напиши: «напомни через час выключить духовку».")
            return
        lines = [f"⏰ <b>Напоминания</b>"]
        buttons = []
        for rid, due, text in rows:
            when = datetime.fromtimestamp(due, timezone.utc).astimezone(reminders.tz)
            lines.append(f"#{rid} · {when:%d.%m %H:%M} — {_escape(text)}")
            buttons.append([InlineKeyboardButton(text=f"❌ #{rid}", callback_data=f"unremind:{rid}")])
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.message(Command("export"))
    async def cmd_export(message: Message) -> None:
        if not user_ok(message):
            return
        md = _transcript(await storage.history(message.chat.id))
        if not md:
            await message.answer("Диалог пока пустой — экспортировать нечего.")
            return
        stamp = datetime.now(reminders.tz).strftime("%Y-%m-%d_%H-%M")
        await message.answer_document(
            BufferedInputFile(md.encode(), filename=f"dialog_{stamp}.md"), caption="📥 Текущий диалог"
        )

    @router.callback_query(F.data.startswith("mode:"))
    async def on_mode(cb: CallbackQuery) -> None:
        key = cb.data.split(":", 1)[1]
        if not allowed(cb.from_user.id) or key not in PERSONAS:
            await cb.answer("⛔" if not allowed(cb.from_user.id) else None)
            return
        chat_id = cb.message.chat.id
        # A new persona means a new system prompt, so it starts a fresh dialog
        # instead of rewriting the one in progress.
        await storage.set_field(chat_id, "persona", key)
        await storage.reset(chat_id)
        await cb.message.edit_text(f"Режим: <b>{PERSONAS[key].title}</b>\nНовый диалог начат — пиши!")
        await cb.answer()

    @router.callback_query(F.data.startswith("effort:"))
    async def on_effort(cb: CallbackQuery) -> None:
        effort = cb.data.split(":", 1)[1]
        if not allowed(cb.from_user.id) or effort not in EFFORTS:
            await cb.answer("⛔" if not allowed(cb.from_user.id) else None)
            return
        await storage.set_field(cb.message.chat.id, "effort", effort)
        await cb.message.edit_text(f"Глубина размышлений: <b>{EFFORT_TITLES[effort]}</b>")
        await cb.answer()

    @router.callback_query(F.data.startswith("unremind:"))
    async def on_unremind(cb: CallbackQuery) -> None:
        if not allowed(cb.from_user.id):
            await cb.answer("⛔")
            return
        rid = int(cb.data.split(":", 1)[1])
        ok = await storage.cancel_reminder(cb.message.chat.id, rid)
        await cb.answer(f"Напоминание #{rid} отменено" if ok else "Уже неактуально")

    @router.callback_query(F.data == "stop")
    async def on_stop(cb: CallbackQuery) -> None:
        if not allowed(cb.from_user.id):
            await cb.answer("⛔")
            return
        task = running.get(cb.message.chat.id)
        if task and not task.done():
            task.cancel()
            await cb.answer("Останавливаю")
        else:
            await cb.answer()

    @router.callback_query(F.data.startswith("act:"))
    async def on_action(cb: CallbackQuery, bot: Bot) -> None:
        action = ACTIONS.get(cb.data.split(":", 1)[1])
        if not allowed(cb.from_user.id) or not action:
            await cb.answer("⛔" if not allowed(cb.from_user.id) else None)
            return
        await cb.answer(action[0])
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        chat_id = cb.message.chat.id
        async with locks[chat_id]:
            await _answer(cb.message, bot, chat_id, [{"type": "text", "text": action[1]}])

    @router.message(F.voice | F.video_note | F.audio)
    async def on_voice(message: Message, bot: Bot) -> None:
        if user_ok(message) and _addressed(message, await _me(bot)):
            await message.reply("🎙 Голосовые пока не понимаю — напиши текстом, пожалуйста.")

    @router.message(F.text | F.photo | F.document)
    async def on_message(message: Message, bot: Bot) -> None:
        if not user_ok(message):
            if message.chat.type == ChatType.PRIVATE and message.from_user:
                await message.answer(f"⛔ Доступ закрыт. Твой id: <code>{message.from_user.id}</code>")
            return

        if message.media_group_id:
            # Telegram delivers an album as separate messages; gather them into one turn.
            group = albums.setdefault(message.media_group_id, [])
            group.append(message)
            if len(group) > 1:
                return
            await asyncio.sleep(ALBUM_WAIT)
            parts = albums.pop(message.media_group_id)
        else:
            parts = [message]

        me = await _me(bot)
        if not any(_addressed(m, me) for m in parts):
            return

        try:
            content = await _user_content(parts, bot, me)
        except ValueError as e:
            await message.reply(f"⚠️ {e}")
            return
        if not content:
            return

        chat_id = message.chat.id
        async with locks[chat_id]:
            await _answer(message, bot, chat_id, content)

    async def _answer(message: Message, bot: Bot, chat_id: int, content: list[dict]) -> None:
        settings = await storage.settings(chat_id)
        persona = get_persona(settings.persona)
        stamp = f"[{reminders.now():%Y-%m-%d %H:%M, %A} ({cfg.timezone})]"
        user_turn = {"role": "user", "content": [{"type": "text", "text": stamp}, *content]}
        history = await storage.history(chat_id) + [user_turn]

        await _react(message, "👀")
        stop_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏹ Стоп", callback_data="stop")]])
        placeholder = await message.reply("🤔 Думаю…", reply_markup=stop_kb)
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        last_edit = 0.0
        partial = ""

        async def on_text(text: str) -> None:
            nonlocal last_edit, partial
            partial = text
            now = time.monotonic()
            if now - last_edit < EDIT_INTERVAL:
                return
            last_edit = now
            try:
                await placeholder.edit_text(preview(text) + " ▌", parse_mode=None, reply_markup=stop_kb)
            except TelegramRetryAfter as e:
                last_edit = now + e.retry_after
            except TelegramBadRequest:
                pass

        async def run_tool(name: str, args: dict) -> str:
            return await reminders.run_tool(chat_id, name, args)

        task = asyncio.create_task(
            claude.reply(persona.system, history, settings.effort or cfg.default_effort, on_text, run_tool)
        )
        running[chat_id] = task
        try:
            await asyncio.wait({task})
        finally:
            running.pop(chat_id, None)
            typing.cancel()

        if task.cancelled():
            # Nothing is saved: the dialog continues as if the question was never asked.
            text = (partial.strip() + "\n\n" if partial.strip() else "") + "⏹ Остановлено."
            await _edit(placeholder, preview(text), parse_mode=None)
            return

        error = task.exception()
        if error:
            await _edit(placeholder, _error_text(error, chat_id))
            return

        reply = task.result()
        await storage.add_usage(chat_id, reply.input_tokens, reply.output_tokens, reply.cached_tokens)
        if reply.refused:
            await _edit(placeholder, "🙅 С этим помочь не могу. Попробуй переформулировать.")
            return

        await storage.append(chat_id, [user_turn, *reply.turns])

        text = reply.text.strip() or "🤷 Пустой ответ. Попробуй переформулировать."
        if reply.truncated:
            text += "\n\n_…ответ обрезан по лимиту длины. Напиши «продолжай»._"
        if reply.searches:
            text += f"\n\n🌐 _поисков в интернете: {reply.searches}_"
        await _deliver(message, placeholder, text)

    return router


def _error_text(error: BaseException, chat_id: int) -> str:
    if isinstance(error, anthropic.RateLimitError):
        return "⏳ Слишком много запросов к Claude. Попробуй через минуту."
    if isinstance(error, anthropic.BadRequestError):
        log.warning("bad request in chat %s: %s", chat_id, error.message)
        return ("⚠️ Claude не принял запрос. Если диалог очень длинный или в нём тяжёлые файлы — "
                "начни новый: /new")
    if isinstance(error, anthropic.APIStatusError):
        log.error("Claude API error %s in chat %s: %s", error.status_code, chat_id, error.message)
        return f"⚠️ Ошибка Claude API ({error.status_code}). Попробуй ещё раз."
    if isinstance(error, anthropic.APIConnectionError):
        return "📡 Не достучался до Claude. Попробуй ещё раз."
    log.error("reply failed in chat %s", chat_id, exc_info=error)
    return "💥 Что-то пошло не так. Попробуй ещё раз."


async def _deliver(message: Message, placeholder: Message, text: str) -> None:
    chunks = split_markdown(text)
    actions = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=title, callback_data=f"act:{key}") for key, (title, _) in ACTIONS.items()
    ]])
    for i, chunk in enumerate(chunks):
        markup = actions if i == len(chunks) - 1 else None
        for body, mode in ((to_html(chunk), ParseMode.HTML), (chunk, None)):
            try:
                if i == 0:
                    await placeholder.edit_text(body, parse_mode=mode, reply_markup=markup,
                                                disable_web_page_preview=True)
                else:
                    await message.answer(body, parse_mode=mode, reply_markup=markup,
                                         disable_web_page_preview=True)
                break
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    break
                # The converter produced HTML Telegram rejects: fall back to plain text.
                log.info("HTML rejected (%s), sending plain text", e)


async def _edit(msg: Message, text: str, parse_mode: str | None = ParseMode.HTML) -> None:
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=None)
    except TelegramBadRequest:
        pass


async def _react(message: Message, emoji: str) -> None:
    try:
        await message.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramAPIError:
        pass  # reactions can be disabled in the chat, or the message is the bot's own


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.debug("typing indicator failed", exc_info=True)


_ME = {}


async def _me(bot: Bot):
    if bot.id not in _ME:
        _ME[bot.id] = await bot.get_me()
    return _ME[bot.id]


def _addressed(message: Message, me) -> bool:
    """In private chats always; in groups only when mentioned or replied to."""
    if message.chat.type == ChatType.PRIVATE:
        return True
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id:
        return True
    text = message.text or message.caption or ""
    return f"@{me.username}".lower() in text.lower()


async def _user_content(parts: list[Message], bot: Bot, me) -> list[dict]:
    first = parts[0]
    blocks: list[dict] = []

    # Replying to someone else's message (or quoting part of it) brings it in as context.
    # Replies to the bot itself need nothing extra: that answer is already in the history.
    reply = first.reply_to_message
    if reply and not (reply.from_user and reply.from_user.id == me.id):
        quoted = (first.quote and first.quote.text) or reply.text or reply.caption or ""
        author = reply.from_user.full_name if reply.from_user else "кто-то"
        if quoted:
            blocks.append({"type": "text", "text": f'<quoted from="{author}">\n{quoted[:MAX_QUOTE]}\n</quoted>'})
        if reply.photo and not any(m.photo or m.document for m in parts):
            blocks.append(await _photo_block(bot, reply))

    for m in parts:
        if m.photo:
            blocks.append(await _photo_block(bot, m))
        elif m.document:
            blocks.append(await _document_block(bot, m))

    text = "\n".join(t for t in ((m.text or m.caption or "") for m in parts) if t)
    text = text.replace(f"@{me.username}", "").strip()
    if first.chat.type != ChatType.PRIVATE and first.from_user:
        text = f"[{first.from_user.full_name}]: {text}" if text else f"[{first.from_user.full_name}]"
    if text:
        blocks.append({"type": "text", "text": text})
    elif blocks:
        blocks.append({"type": "text", "text": "Посмотри, что я прислал, и коротко прокомментируй."})
    return blocks


async def _photo_block(bot: Bot, message: Message) -> dict:
    photo = message.photo[-1]
    return _image(await _download(bot, photo.file_id, photo.file_size), "image/jpeg")


async def _document_block(bot: Bot, message: Message) -> dict:
    doc = message.document
    mime = doc.mime_type or ""
    name = doc.file_name or "file"
    if mime == "application/pdf":
        data = await _download(bot, doc.file_id, doc.file_size)
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(data)},
            "title": name,
        }
    if mime in IMAGE_TYPES:
        return _image(await _download(bot, doc.file_id, doc.file_size), mime)
    if mime.startswith("text/") or name.lower().endswith(TEXT_SUFFIXES):
        data = await _download(bot, doc.file_id, doc.file_size)
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            body = data.decode("cp1251", errors="replace")
        return {"type": "text", "text": f'<file name="{name}">\n{body}\n</file>'}
    raise ValueError("Такой формат не поддерживаю. Пришли фото, PDF или текстовый файл.")


async def _download(bot: Bot, file_id: str, size: int | None) -> bytes:
    if size and size > MAX_FILE:
        raise ValueError("Файл больше 20 МБ — Telegram не даёт ботам скачивать такие.")
    buf = await bot.download(file_id)
    return buf.read()


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode()


def _image(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(data)}}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}, \w+ \([^)]*\)\]$")


def _transcript(history: list[dict]) -> str:
    """Readable Markdown of a dialog: user and assistant text, no tool plumbing."""
    out = []
    for turn in history:
        texts, files = [], 0
        for block in turn["content"]:
            if block.get("type") == "text":
                texts.append(block["text"])
            elif block.get("type") in ("image", "document"):
                files += 1
        if turn["role"] == "user" and texts and _STAMP.match(texts[0]):
            texts = texts[1:]
        body = "\n\n".join(t for t in texts if t.strip())
        if files:
            body = f"_[вложений: {files}]_\n\n{body}"
        if body.strip():
            out.append(f"### {'🧑 Ты' if turn['role'] == 'user' else '🤖 Claude'}\n\n{body}")
    return "\n\n---\n\n".join(out)


def _choices(options: dict[str, str], prefix: str, current: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if key == current else "") + title, callback_data=f"{prefix}:{key}")
        for key, title in options.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)])
