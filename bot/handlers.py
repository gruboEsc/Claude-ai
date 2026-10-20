import base64
import logging
import time
from datetime import datetime, timezone

import anthropic
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .brain import Companion
from .claude import cost
from .config import Config

log = logging.getLogger(__name__)

SUB_PERIOD = 30 * 24 * 3600  # the only subscription period Telegram supports
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def setup(cfg: Config, companion: Companion) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    storage = companion.storage
    name = cfg.companion_name

    def allowed(user_id: int) -> bool:
        return not cfg.allowed_users or user_id in cfg.allowed_users or user_id in cfg.admin_ids

    async def paywall(message: Message) -> None:
        await message.answer(
            f"Бесплатные сообщения закончились.\n\n"
            f"Чтобы продолжить общение с {name}, оформи подписку: {cfg.price_stars} ⭐ в месяц. "
            "Отменить можно в любой момент в /status — деньги за текущий месяц не спишутся повторно.",
            reply_markup=await _subscribe_kb(message.bot),
        )

    async def _subscribe_kb(bot: Bot) -> InlineKeyboardMarkup:
        link = await bot.create_invoice_link(
            title=f"Подписка: общение с {name}",
            description="Безлимитное общение на 30 дней, сообщения первым и напоминания. Продлевается автоматически.",
            payload="sub",
            currency="XTR",
            prices=[LabeledPrice(label="30 дней", amount=cfg.price_stars)],
            subscription_period=SUB_PERIOD,
        )
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"⭐ Подписаться за {cfg.price_stars}", url=link)]])

    @router.message(CommandStart())
    async def cmd_start(message: Message, bot: Bot) -> None:
        uid = message.from_user.id
        if not allowed(uid):
            await message.answer("Бот пока в закрытом режиме.")
            return
        new = await storage.is_new(uid)
        user = await storage.user(uid)
        if not new and user.system:
            await message.answer("Я тут 🙂 Пиши.")
            return
        await message.answer(
            f"Привет! Здесь можно просто поговорить — с {name}.\n\n"
            "Пару честных слов заранее: я ИИ-собеседник. Я запоминаю, что ты рассказываешь, "
            "чтобы общаться как старый знакомый, и иногда пишу первым — это можно выключить в /settings. "
            "Стереть всё, что я знаю, — /forget. Я не заменяю врача или психолога.\n\n"
            f"Первые {cfg.free_messages} сообщений бесплатно."
        )
        await companion.write_first(
            bot, uid,
            "[Служебно: это самое начало вашего знакомства, человек только что запустил бота. "
            "Поздоровайся, коротко представься по-своему и спроси, как к нему обращаться.]",
        )

    @router.message(Command("status"))
    async def cmd_status(message: Message, bot: Bot) -> None:
        user = await storage.user(message.from_user.id)
        if companion.subscribed(user):
            until = datetime.fromtimestamp(user.paid_until, timezone.utc).astimezone(await companion.tools.tz(user.user_id))
            text = f"⭐ Подписка активна до {until:%d.%m.%Y}."
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Отменить продление", callback_data="unsub")
            ]]) if user.charge_id else None
            await message.answer(text, reply_markup=kb)
        else:
            left = max(cfg.free_messages - user.used, 0)
            await message.answer(
                f"Бесплатных сообщений осталось: {left} из {cfg.free_messages}.\n"
                f"Подписка — {cfg.price_stars} ⭐ в месяц.",
                reply_markup=await _subscribe_kb(bot),
            )

    @router.message(Command("subscribe"))
    async def cmd_subscribe(message: Message, bot: Bot) -> None:
        await message.answer(
            f"Подписка: {cfg.price_stars} ⭐ в месяц, без ограничений по сообщениям. "
            "Продлевается автоматически, отменить можно в /status.",
            reply_markup=await _subscribe_kb(bot),
        )

    @router.callback_query(F.data == "unsub")
    async def on_unsub(cb: CallbackQuery, bot: Bot) -> None:
        user = await storage.user(cb.from_user.id)
        if user.charge_id:
            await bot.edit_user_star_subscription(cb.from_user.id, user.charge_id, is_canceled=True)
        await cb.message.edit_text("Продление отменено. Подписка будет работать до конца оплаченного месяца.")
        await cb.answer()

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        user = await storage.user(message.from_user.id)
        await message.answer(
            f"{name} иногда пишет первым, если ты долго молчишь (только при подписке, не чаще раза в день, "
            f"с {cfg.checkin_from_hour}:00 до {cfg.checkin_to_hour}:00).",
            reply_markup=_checkins_kb(bool(user.checkins)),
        )

    @router.callback_query(F.data.startswith("checkins:"))
    async def on_checkins(cb: CallbackQuery) -> None:
        on = cb.data.endswith(":on")
        await storage.update(cb.from_user.id, checkins=int(on))
        await cb.message.edit_reply_markup(reply_markup=_checkins_kb(on))
        await cb.answer("Включено" if on else "Выключено")

    @router.message(Command("forget"))
    async def cmd_forget(message: Message) -> None:
        await message.answer(
            "Стереть всю переписку и всё, что я о тебе запомнил? Это нельзя отменить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Да, стереть", callback_data="forget:yes"),
                InlineKeyboardButton(text="Нет", callback_data="forget:no"),
            ]]),
        )

    @router.callback_query(F.data.startswith("forget:"))
    async def on_forget(cb: CallbackQuery) -> None:
        if cb.data.endswith(":yes"):
            async with companion.locks[cb.from_user.id]:
                await storage.forget_everything(cb.from_user.id)
            await cb.message.edit_text("Готово, всё стёрто. Напиши /start, чтобы познакомиться заново.")
        else:
            await cb.message.edit_text("Ничего не трогаю 🙂")
        await cb.answer()

    @router.message(Command("paysupport"))
    async def cmd_paysupport(message: Message) -> None:
        contact = f" Напиши {cfg.support}." if cfg.support else ""
        await message.answer(
            "Вопросы по оплате и возвратам: опиши проблему и приложи дату платежа." + contact
        )

    @router.message(Command("terms"))
    async def cmd_terms(message: Message) -> None:
        await message.answer(
            f"{name} — ИИ-собеседник для дружеского общения. Это не медицинская и не психологическая помощь. "
            "Переписка и заметки хранятся на сервере, чтобы собеседник помнил контекст; стереть их — /forget. "
            f"Подписка — {cfg.price_stars} ⭐ за 30 дней с автопродлением, отмена в /status. "
            "В экстренной ситуации звони 112."
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(
            f"Просто пиши — {name} ответит. Можно присылать фото.\n\n"
            "/status — подписка\n/settings — писать ли тебе первым\n"
            "/forget — стереть всё\n/terms — условия\n/paysupport — помощь с оплатой"
        )

    @router.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        if message.from_user.id not in cfg.admin_ids:
            return
        s = await storage.admin_stats(int(time.time()))
        spent = cost(cfg.model, s["inp"], s["out"], s["cached"])
        await message.answer(
            f"👥 Пользователей: {s['users']} · активных за 7 дней: {s['active']}\n"
            f"⭐ Подписчиков: {s['subs']} · Stars за 30 дней: {s['month']} · всего: {s['stars']}\n"
            f"🔢 Токенов: вход {s['inp']:,} · кэш {s['cached']:,} · выход {s['out']:,}\n"
            + (f"💸 Расход на API: ≈ ${spent:.2f}" if spent is not None else "")
        )

    @router.pre_checkout_query()
    async def on_pre_checkout(query: PreCheckoutQuery) -> None:
        await query.answer(ok=True)

    @router.message(F.successful_payment)
    async def on_paid(message: Message) -> None:
        p = message.successful_payment
        uid = message.from_user.id
        if not await storage.record_payment(p.telegram_payment_charge_id, uid, p.total_amount, bool(p.is_recurring)):
            return  # duplicate delivery
        user = await storage.user(uid)
        until = p.subscription_expiration_date or max(user.paid_until, int(time.time())) + SUB_PERIOD
        await storage.update(uid, paid_until=int(until), charge_id=p.telegram_payment_charge_id)
        if not p.is_recurring or p.is_first_recurring:
            await message.answer("Спасибо! ⭐ Подписка оформлена — теперь можно общаться без ограничений.")

    @router.message(F.voice | F.video_note | F.audio)
    async def on_voice(message: Message) -> None:
        await message.answer("Голосовые пока не слушаю 🙈 Напиши, пожалуйста, текстом.")

    @router.message(F.text | F.photo | F.sticker | F.document)
    async def on_message(message: Message, bot: Bot) -> None:
        uid = message.from_user.id
        if not allowed(uid):
            await message.answer("Бот пока в закрытом режиме.")
            return
        user = await storage.user(uid)
        if not companion.has_access(user):
            await paywall(message)
            return
        try:
            content = await _content(message, bot)
        except ValueError as e:
            await message.answer(str(e))
            return
        await storage.user_wrote(uid, int(time.time()), counted=not companion.subscribed(user))
        try:
            await companion.converse(bot, uid, content)
        except anthropic.RateLimitError:
            await message.answer("Ой, что-то я подвисаю… напиши ещё раз через минутку 🙏")
        except anthropic.APIError:
            log.exception("Claude API error for %s", uid)
            await message.answer("Что-то со связью у меня… напиши ещё раз, пожалуйста.")

    return router


def _checkins_kb(on: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=("✅ " if on else "") + "Пиши мне первым", callback_data="checkins:on"),
        InlineKeyboardButton(text=("✅ " if not on else "") + "Не пиши первым", callback_data="checkins:off"),
    ]])


async def _content(message: Message, bot: Bot) -> list[dict]:
    blocks: list[dict] = []
    if message.photo:
        photo = message.photo[-1]
        blocks.append(_image(await _download(bot, photo.file_id, photo.file_size), "image/jpeg"))
    elif message.document:
        doc = message.document
        if doc.mime_type not in IMAGE_TYPES:
            raise ValueError("Я пока понимаю только текст, фото и стикеры 🙂")
        blocks.append(_image(await _download(bot, doc.file_id, doc.file_size), doc.mime_type))
    elif message.sticker:
        blocks.append({"type": "text", "text": f"[прислал(а) стикер {message.sticker.emoji or ''}]"})

    text = message.text or message.caption
    if message.forward_origin:
        text = f"[пересланное сообщение]\n{text or ''}"
    if text:
        blocks.append({"type": "text", "text": text})
    elif message.photo or message.document:
        blocks.append({"type": "text", "text": "[прислал(а) фото без подписи]"})
    return blocks


async def _download(bot: Bot, file_id: str, size: int | None) -> bytes:
    if size and size > MAX_FILE:
        raise ValueError("Файл слишком большой, Telegram не даёт мне его открыть 😔")
    return (await bot.download(file_id)).read()


def _image(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode()}}
