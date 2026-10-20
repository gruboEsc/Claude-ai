import base64
import logging
import re
import time
from datetime import datetime, timezone

import anthropic
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .billing import Billing
from .brain import Companion
from .claude import cost
from .config import Config
from .yookassa import YooKassaError

log = logging.getLogger(__name__)

SUB_PERIOD = 30 * 86400  # the only Stars subscription period Telegram supports
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def setup(cfg: Config, companion: Companion, billing: Billing) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    storage = companion.storage
    name = cfg.companion_name

    def allowed(user_id: int) -> bool:
        return not cfg.allowed_users or user_id in cfg.allowed_users or user_id in cfg.admin_ids

    plans = cfg.plans()

    def prices() -> str:
        return "; ".join(
            f"{p.title.lower()} — {p.rub} ₽ или {p.stars} ⭐" if billing.enabled else f"{p.title.lower()} — {p.stars} ⭐"
            for p in plans.values()
        )

    async def paywall(message: Message) -> None:
        await message.answer(
            f"Бесплатные сообщения закончились.\n\n"
            f"Чтобы продолжить общение с {name}, выбери подписку: {prices()}.",
            reply_markup=await _subscribe_kb(message.bot),
        )

    async def _stars_link(bot: Bot, days: int) -> str:
        plan = plans[days]
        monthly = days == 30  # Telegram only has 30-day Stars subscriptions
        return await bot.create_invoice_link(
            title=f"{name}: {plan.title.lower()}",
            description=f"Общение на {days} дней, сообщения первым и напоминания."
                        + (" Продлевается автоматически." if monthly else ""),
            payload=f"plan{days}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{days} дней", amount=plan.stars)],
            subscription_period=SUB_PERIOD if monthly else None,
        )

    async def _subscribe_kb(bot: Bot) -> InlineKeyboardMarkup:
        rows = []
        for days, plan in plans.items():
            row = []
            if billing.enabled:
                row.append(InlineKeyboardButton(text=f"💳 {plan.title} — {plan.rub} ₽", callback_data=f"yk:pay:{days}"))
            row.append(InlineKeyboardButton(text=f"⭐ {plan.title} — {plan.stars}", url=await _stars_link(bot, days)))
            rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_payment_link(message: Message, bot: Bot, user_id: int, email: str, days: int) -> None:
        plan = plans[days]
        try:
            url = await billing.start(bot, user_id, email, plan)
        except YooKassaError as e:
            log.error("YooKassa payment for %s failed: %s", user_id, e)
            await message.answer("Не получилось создать платёж 😔 Попробуй чуть позже или оплати звёздами.")
            return
        consent = (f" Оплачивая, ты соглашаешься на автопродление: каждые {days} дней с карты будет списываться "
                   f"{plan.rub} ₽. Отключить можно в /status." if cfg.yk_autopay else "")
        await message.answer(
            f"Подписка на {days} дней — {plan.rub} ₽. Чек придёт на {email}.{consent}\n\n"
            "После оплаты вернись сюда — я сам замечу платёж в течение минуты, или нажми «Я оплатил».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data="yk:check")],
            ]),
        )

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
            text = f"✨ Подписка активна до {until:%d.%m.%Y}."
            if user.autopay:
                plan = plans.get(user.plan_days, plans[30])
                text += f"\nАвтопродление с карты включено: {plan.rub} ₽ раз в {plan.days} дней."
            rows = []
            if user.charge_id or user.autopay:
                rows.append([InlineKeyboardButton(text="Отменить продление", callback_data="unsub")])
            if not user.autopay:
                rows.append([InlineKeyboardButton(text="Продлить заранее", callback_data="subscribe")])
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            left = max(cfg.free_messages - user.used, 0)
            await message.answer(
                f"Бесплатных сообщений осталось: {left} из {cfg.free_messages}.\n"
                f"Подписка — {prices()}.",
                reply_markup=await _subscribe_kb(bot),
            )

    @router.message(Command("subscribe"))
    async def cmd_subscribe(message: Message, bot: Bot) -> None:
        await message.answer(
            f"Подписка: {prices()}. Продление отменяется в /status.",
            reply_markup=await _subscribe_kb(bot),
        )

    @router.callback_query(F.data == "subscribe")
    async def on_subscribe(cb: CallbackQuery, bot: Bot) -> None:
        await cmd_subscribe(cb.message, bot)
        await cb.answer()

    @router.callback_query(F.data == "unsub")
    async def on_unsub(cb: CallbackQuery, bot: Bot) -> None:
        user = await storage.user(cb.from_user.id)
        if user.charge_id:
            try:
                await bot.edit_user_star_subscription(cb.from_user.id, user.charge_id, is_canceled=True)
            except TelegramBadRequest as e:
                log.info("Stars subscription cancel for %s: %s", cb.from_user.id, e)
        await storage.update(cb.from_user.id, autopay=0, pay_method_id=None, charge_id=None)
        await cb.message.edit_text("Продление отменено. Подписка будет работать до конца оплаченного срока.")
        await cb.answer()

    @router.callback_query(F.data.startswith("yk:pay:"))
    async def on_yk_pay(cb: CallbackQuery, bot: Bot) -> None:
        await cb.answer()
        days = int(cb.data.rsplit(":", 1)[1])
        if days not in plans:
            return
        user = await storage.user(cb.from_user.id)
        if user.email:
            await send_payment_link(cb.message, bot, user.user_id, user.email, days)
            return
        await storage.update(user.user_id, awaiting_email=days)
        await cb.message.answer("Пришли, пожалуйста, email — на него придёт чек об оплате (так требует закон).")

    @router.callback_query(F.data == "yk:check")
    async def on_yk_check(cb: CallbackQuery, bot: Bot) -> None:
        try:
            paid = await billing.check_user(bot, cb.from_user.id)
        except YooKassaError as e:
            log.warning("payment check for %s failed: %s", cb.from_user.id, e)
            paid = False
        if paid:
            await cb.answer("Оплата прошла!")
            await cb.message.edit_reply_markup(reply_markup=None)
        else:
            user = await storage.user(cb.from_user.id)
            await cb.answer(
                "Подписка уже активна 🙂" if companion.subscribed(user)
                else "Пока не вижу оплату. Если уже оплатил — подожди минутку.",
                show_alert=True,
            )

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
            f"Подписка: {prices()}. Месячная через Stars (и через карту, если включено автопродление) "
            "продлевается автоматически; отмена в /status. "
            f"В подписке есть разумный лимит общения в день и в месяц — его хватает на долгие ежедневные разговоры. "
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
            f"💳 ЮKassa за 30 дней: {s['rub_month']:,.0f} ₽\n"
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
        days = 14 if p.invoice_payload == "plan14" else 30
        if p.subscription_expiration_date:
            # Never shorten time already paid another way (e.g. through YooKassa).
            until = max(int(p.subscription_expiration_date), user.paid_until)
            await storage.update(uid, paid_until=until, plan_days=days)
        else:
            await storage.extend(uid, days, int(time.time()))
        await storage.update(uid, charge_id=p.telegram_payment_charge_id)
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
        if user.awaiting_email:
            await storage.update(uid, awaiting_email=0)
            text = (message.text or "").strip()
            if EMAIL.match(text) and user.awaiting_email in plans:
                await storage.update(uid, email=text)
                await send_payment_link(message, bot, uid, text, user.awaiting_email)
                return
            # Not an email: they changed their mind, so treat it as a normal message.
        if not companion.has_access(user):
            await paywall(message)
            return
        limit = await companion.over_limit(user)
        if limit:
            kind, resets = limit
            await message.answer(
                "Мы сегодня очень много проговорили 🙂 Дневной лимит общения закончился — "
                "давай продолжим завтра, я буду ждать." if kind == "day" else
                f"Лимит общения на этот оплаченный срок закончился. Он обновится {resets:%d.%m}."
            )
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
