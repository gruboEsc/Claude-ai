import asyncio
import base64
import logging
import time
from collections import defaultdict

import anthropic
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .claude import Claude
from .config import EFFORTS, Config
from .db import Storage
from .formatting import preview, split_markdown, to_html
from .personas import PERSONAS, get_persona

log = logging.getLogger(__name__)

EDIT_INTERVAL = 1.2  # seconds between streaming edits; Telegram throttles faster edits
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit
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

HELP = """\
<b>Привет! Я бот на Claude 🤖</b>

Просто пиши — я помню весь диалог.
• 📷 Пришли фото — опишу, решу задачу с картинки, прочитаю текст
• 📄 Пришли PDF или файл с кодом/текстом — разберу и отвечу по нему
• 🌐 Сам ищу в интернете, когда нужны свежие данные

<b>Команды</b>
/new — начать новый диалог
/mode — выбрать режим (программист, переводчик, копирайтер…)
/effort — насколько глубоко думать
/stats — статистика диалога
/help — эта справка

В группах отвечаю, когда меня упоминают или отвечают на моё сообщение."""


def setup(cfg: Config, storage: Storage, claude: Claude) -> Router:
    router = Router()
    locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def allowed(user_id: int | None) -> bool:
        return not cfg.allowed_users or user_id in cfg.allowed_users

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if not allowed(message.from_user and message.from_user.id):
            await message.answer(f"⛔ Доступ закрыт. Твой id: <code>{message.from_user.id}</code>")
            return
        await message.answer(HELP)

    @router.message(Command("new", "reset"))
    async def cmd_new(message: Message) -> None:
        if not allowed(message.from_user and message.from_user.id):
            return
        await storage.reset(message.chat.id)
        await message.answer("🧹 Начали с чистого листа. О чём поговорим?")

    @router.message(Command("mode"))
    async def cmd_mode(message: Message) -> None:
        if not allowed(message.from_user and message.from_user.id):
            return
        current = (await storage.settings(message.chat.id)).persona
        await message.answer(
            f"Сейчас: <b>{get_persona(current).title}</b>\nВыбери режим (диалог начнётся заново):",
            reply_markup=_keyboard({k: p.title for k, p in PERSONAS.items()}, "mode", get_persona(current).key),
        )

    @router.message(Command("effort"))
    async def cmd_effort(message: Message) -> None:
        if not allowed(message.from_user and message.from_user.id):
            return
        current = (await storage.settings(message.chat.id)).effort or cfg.default_effort
        await message.answer(
            "Насколько глубоко думать над ответами?\nБыстрее — дешевле, глубже — умнее.",
            reply_markup=_keyboard({e: EFFORT_TITLES[e] for e in EFFORTS}, "effort", current),
        )

    @router.message(Command("stats"))
    async def cmd_stats(message: Message) -> None:
        if not allowed(message.from_user and message.from_user.id):
            return
        s = await storage.settings(message.chat.id)
        await message.answer(
            "📊 <b>Статистика чата</b>\n"
            f"Режим: {get_persona(s.persona).title}\n"
            f"Глубина: {EFFORT_TITLES[s.effort or cfg.default_effort]}\n"
            f"Сообщений в текущем диалоге: {s.turns}\n"
            f"Запросов к Claude всего: {s.requests}\n"
            f"Токенов: вход {s.input_tokens:,} · из кэша {s.cached_tokens:,} · выход {s.output_tokens:,}\n"
            f"Модель: <code>{cfg.model}</code>"
        )

    @router.callback_query(F.data.startswith("mode:"))
    async def on_mode(cb: CallbackQuery) -> None:
        if not allowed(cb.from_user.id):
            await cb.answer("⛔", show_alert=True)
            return
        key = cb.data.split(":", 1)[1]
        if key not in PERSONAS:
            await cb.answer()
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
        if not allowed(cb.from_user.id):
            await cb.answer("⛔", show_alert=True)
            return
        effort = cb.data.split(":", 1)[1]
        if effort not in EFFORTS:
            await cb.answer()
            return
        await storage.set_field(cb.message.chat.id, "effort", effort)
        await cb.message.edit_text(f"Глубина размышлений: <b>{EFFORT_TITLES[effort]}</b>")
        await cb.answer()

    @router.message(F.voice | F.video_note | F.audio)
    async def on_voice(message: Message) -> None:
        if allowed(message.from_user and message.from_user.id) and _addressed(message, await _me(message.bot)):
            await message.reply("🎙 Голосовые пока не понимаю — напиши текстом, пожалуйста.")

    @router.message(F.text | F.photo | F.document)
    async def on_message(message: Message, bot: Bot) -> None:
        if not message.from_user or not allowed(message.from_user.id):
            if message.chat.type == ChatType.PRIVATE:
                await message.answer(f"⛔ Доступ закрыт. Твой id: <code>{message.from_user.id}</code>")
            return
        me = await _me(bot)
        if not _addressed(message, me):
            return

        try:
            content = await _user_content(message, bot, me.username)
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
        user_turn = {"role": "user", "content": content}
        history = await storage.history(chat_id) + [user_turn]

        placeholder = await message.reply("🤔 Думаю…")
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        last_edit = 0.0
        shown = ""

        async def on_text(text: str) -> None:
            nonlocal last_edit, shown
            now = time.monotonic()
            if now - last_edit < EDIT_INTERVAL:
                return
            last_edit = now
            snippet = preview(text) + " ▌"
            if snippet == shown:
                return
            shown = snippet
            try:
                await placeholder.edit_text(snippet, parse_mode=None)
            except TelegramRetryAfter as e:
                last_edit = now + e.retry_after
            except TelegramBadRequest:
                pass

        try:
            reply = await claude.reply(
                persona.system, history, settings.effort or cfg.default_effort, on_text
            )
        except anthropic.RateLimitError:
            await placeholder.edit_text("⏳ Слишком много запросов к Claude. Попробуй через минуту.")
            return
        except anthropic.BadRequestError as e:
            log.warning("bad request in chat %s: %s", chat_id, e.message)
            await placeholder.edit_text(
                "⚠️ Claude не принял запрос. Если диалог очень длинный или в нём тяжёлые файлы — "
                "начни новый: /new"
            )
            return
        except anthropic.APIStatusError as e:
            log.exception("Claude API error")
            await placeholder.edit_text(f"⚠️ Ошибка Claude API ({e.status_code}). Попробуй ещё раз.")
            return
        except anthropic.APIConnectionError:
            await placeholder.edit_text("📡 Не достучался до Claude. Попробуй ещё раз.")
            return
        except Exception:
            log.exception("reply failed in chat %s", chat_id)
            await placeholder.edit_text("💥 Что-то пошло не так. Попробуй ещё раз.")
            return
        finally:
            typing.cancel()

        await storage.add_usage(chat_id, reply.input_tokens, reply.output_tokens, reply.cached_tokens)
        if reply.refused:
            await placeholder.edit_text("🙅 С этим помочь не могу. Попробуй переформулировать.")
            return

        await storage.append(chat_id, [user_turn, *reply.turns])

        text = reply.text.strip() or "🤷 Пустой ответ. Попробуй переформулировать."
        if reply.truncated:
            text += "\n\n_…ответ обрезан по лимиту длины. Напиши «продолжай»._"
        if reply.searches:
            text += f"\n\n🌐 _поисков в интернете: {reply.searches}_"
        await _deliver(message, placeholder, text)

    return router


async def _deliver(message: Message, placeholder: Message, text: str) -> None:
    for i, chunk in enumerate(split_markdown(text)):
        try:
            if i == 0:
                await placeholder.edit_text(to_html(chunk), parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True)
            else:
                await message.answer(to_html(chunk), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                continue
            # The converter produced HTML Telegram rejects: fall back to plain text.
            log.info("HTML rejected (%s), sending plain text", e)
            if i == 0:
                await placeholder.edit_text(chunk, parse_mode=None, disable_web_page_preview=True)
            else:
                await message.answer(chunk, parse_mode=None, disable_web_page_preview=True)


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


async def _user_content(message: Message, bot: Bot, username: str) -> list[dict]:
    blocks: list[dict] = []
    text = (message.text or message.caption or "").replace(f"@{username}", "").strip()

    if message.photo:
        photo = message.photo[-1]
        data = await _download(bot, photo.file_id, photo.file_size)
        blocks.append(_image(data, "image/jpeg"))
    elif message.document:
        doc = message.document
        mime = doc.mime_type or ""
        name = doc.file_name or "file"
        if mime == "application/pdf":
            data = await _download(bot, doc.file_id, doc.file_size)
            blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(data)},
                "title": name,
            })
        elif mime in IMAGE_TYPES:
            blocks.append(_image(await _download(bot, doc.file_id, doc.file_size), mime))
        elif mime.startswith("text/") or name.lower().endswith(TEXT_SUFFIXES):
            data = await _download(bot, doc.file_id, doc.file_size)
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                body = data.decode("cp1251", errors="replace")
            blocks.append({"type": "text", "text": f'<file name="{name}">\n{body}\n</file>'})
        else:
            raise ValueError("Такой формат не поддерживаю. Пришли фото, PDF или текстовый файл.")

    if message.chat.type != ChatType.PRIVATE and message.from_user:
        text = f"[{message.from_user.full_name}]: {text}" if text else f"[{message.from_user.full_name}]"
    if text:
        blocks.append({"type": "text", "text": text})
    elif blocks:
        blocks.append({"type": "text", "text": "Посмотри, что я прислал, и коротко прокомментируй."})
    return blocks


async def _download(bot: Bot, file_id: str, size: int | None) -> bytes:
    if size and size > MAX_FILE:
        raise ValueError("Файл больше 20 МБ — Telegram не даёт ботам скачивать такие.")
    buf = await bot.download(file_id)
    return buf.read()


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode()


def _image(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(data)}}


def _keyboard(options: dict[str, str], prefix: str, current: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if key == current else "") + title, callback_data=f"{prefix}:{key}")
        for key, title in options.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)])
