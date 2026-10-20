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
    User as TgUser,
)

from .billing import Billing
from .brain import Companion
from .claude import cost
from .config import Config
from .db import User
from .i18n import LANGS, lang_of, t
from .yookassa import YooKassaError

log = logging.getLogger(__name__)

SUB_PERIOD = 30 * 86400  # the only Stars subscription period Telegram supports
MAX_FILE = 20 * 1024 * 1024  # Bot API download limit
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserFacingError(Exception):
    """Carries an i18n key for a message to show the person."""


def setup(cfg: Config, companion: Companion, billing: Billing) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    storage = companion.storage

    def allowed(user_id: int) -> bool:
        return not cfg.allowed_users or user_id in cfg.allowed_users or user_id in cfg.admin_ids

    async def load(tg: TgUser) -> tuple[User, str]:
        """The stored user and their language, picked from Telegram on first contact."""
        user = await storage.user(tg.id)
        if not user.lang:
            await storage.update(tg.id, lang=lang_of(tg.language_code))
            user = await storage.user(tg.id)
        return user, companion.lang(user)

    def rubles(lang: str) -> bool:
        return billing.enabled and lang == "ru"  # YooKassa takes Russian cards only

    def prices(user: User, lang: str) -> str:
        key = "price_line_rub" if rubles(lang) else "price_line"
        return "; ".join(
            t(lang, key, title=t(lang, f"plan_{d}").lower(), rub=p.rub, stars=p.stars)
            for d, p in cfg.plans(companion.tier(user)).items()
        )

    async def stars_link(bot: Bot, user: User, lang: str, days: int) -> str:
        plan = cfg.plans(companion.tier(user))[days]
        monthly = days == 30  # Telegram only has 30-day Stars subscriptions
        return await bot.create_invoice_link(
            title=f"{companion.name(user)}: {t(lang, f'plan_{days}')}",
            description=t(lang, "invoice_desc", days=days) + (t(lang, "invoice_renews") if monthly else ""),
            payload=f"plan{days}",
            currency="XTR",
            prices=[LabeledPrice(label=t(lang, f"plan_{days}"), amount=plan.stars)],
            subscription_period=SUB_PERIOD if monthly else None,
        )

    async def plans_kb(bot: Bot, user: User, lang: str) -> InlineKeyboardMarkup:
        rows = []
        for days, plan in cfg.plans(companion.tier(user)).items():
            title = t(lang, f"plan_{days}")
            row = []
            if rubles(lang):
                row.append(InlineKeyboardButton(text=t(lang, "btn_card", title=title, rub=plan.rub),
                                                callback_data=f"yk:pay:{days}"))
            row.append(InlineKeyboardButton(text=t(lang, "btn_stars", title=title, stars=plan.stars),
                                            url=await stars_link(bot, user, lang, days)))
            rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_payment_link(message: Message, bot: Bot, user: User, email: str, days: int) -> None:
        lang = companion.lang(user)
        plan = cfg.plans(companion.tier(user))[days]
        try:
            me = await bot.get_me()
            url = await billing.start(user.user_id, email, plan, return_url=f"https://t.me/{me.username}")
        except YooKassaError as e:
            log.error("YooKassa payment for %s failed: %s", user.user_id, e)
            await message.answer(t(lang, "pay_failed"))
            return
        consent = t(lang, "pay_consent", days=days, rub=plan.rub) if cfg.yk_autopay else ""
        await message.answer(
            t(lang, "pay_link", days=days, rub=plan.rub, email=email, consent=consent),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t(lang, "btn_go_pay"), url=url)],
                [InlineKeyboardButton(text=t(lang, "btn_paid"), callback_data="yk:check")],
            ]),
        )

    @router.message(CommandStart())
    async def cmd_start(message: Message, bot: Bot) -> None:
        user, lang = await load(message.from_user)
        if not allowed(user.user_id):
            await message.answer(t(lang, "closed"))
            return
        if user.system:
            await message.answer(t(lang, "back"))
            return
        await message.answer(t(lang, "intro", name=companion.name(user), free=cfg.free_messages))
        await companion.write_first(
            user.user_id,
            "[Service note: this is the very start of your acquaintance — the person has just started the "
            "bot. Say hello, briefly introduce yourself in your own way and ask how to address them.]",
        )

    @router.message(Command("language"))
    async def cmd_language(message: Message) -> None:
        _, lang = await load(message.from_user)
        buttons = [InlineKeyboardButton(text=("✅ " if code == lang else "") + info.native, callback_data=f"lang:{code}")
                   for code, info in LANGS.items()]
        await message.answer(t(lang, "language_pick"), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)]))

    @router.callback_query(F.data.startswith("lang:"))
    async def on_language(cb: CallbackQuery) -> None:
        code = cb.data.split(":", 1)[1]
        if code not in LANGS:
            await cb.answer()
            return
        async with companion.locks[cb.from_user.id]:
            # A new language means a new companion persona, so the frozen prompt is rebuilt.
            await storage.update(cb.from_user.id, lang=code, system=None)
        await cb.message.edit_text(t(code, "language_set"))
        await cb.answer()

    @router.message(Command("status"))
    async def cmd_status(message: Message, bot: Bot) -> None:
        user, lang = await load(message.from_user)
        if companion.subscribed(user):
            until = datetime.fromtimestamp(user.paid_until, timezone.utc).astimezone(await companion.tools.tz(user.user_id))
            text = t(lang, "status_active", date=f"{until:%d.%m.%Y}")
            if user.autopay:
                plan = cfg.plans(companion.tier(user)).get(user.plan_days) or cfg.plans()[30]
                text += t(lang, "status_autopay", rub=plan.rub, days=plan.days)
            rows = []
            if user.charge_id or user.autopay:
                rows.append([InlineKeyboardButton(text=t(lang, "btn_cancel_renew"), callback_data="unsub")])
            if not user.autopay:
                rows.append([InlineKeyboardButton(text=t(lang, "btn_renew_early"), callback_data="subscribe")])
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await message.answer(
                t(lang, "status_trial", left=max(cfg.free_messages - user.used, 0), free=cfg.free_messages,
                  prices=prices(user, lang)),
                reply_markup=await plans_kb(bot, user, lang),
            )

    async def show_plans(message: Message, bot: Bot, tg: TgUser, key: str) -> None:
        user, lang = await load(tg)
        await message.answer(t(lang, key, name=companion.name(user), prices=prices(user, lang)),
                             reply_markup=await plans_kb(bot, user, lang))

    @router.message(Command("subscribe"))
    async def cmd_subscribe(message: Message, bot: Bot) -> None:
        await show_plans(message, bot, message.from_user, "subscribe")

    @router.callback_query(F.data == "subscribe")
    async def on_subscribe(cb: CallbackQuery, bot: Bot) -> None:
        await show_plans(cb.message, bot, cb.from_user, "subscribe")
        await cb.answer()

    @router.callback_query(F.data == "unsub")
    async def on_unsub(cb: CallbackQuery, bot: Bot) -> None:
        user, lang = await load(cb.from_user)
        if user.charge_id:
            try:
                await bot.edit_user_star_subscription(user.user_id, user.charge_id, is_canceled=True)
            except TelegramBadRequest as e:
                log.info("Stars subscription cancel for %s: %s", user.user_id, e)
        await storage.update(user.user_id, autopay=0, pay_method_id=None, charge_id=None)
        await cb.message.edit_text(t(lang, "unsub_done"))
        await cb.answer()

    @router.callback_query(F.data.startswith("yk:pay:"))
    async def on_yk_pay(cb: CallbackQuery, bot: Bot) -> None:
        await cb.answer()
        user, lang = await load(cb.from_user)
        days = int(cb.data.rsplit(":", 1)[1])
        if days not in cfg.plans() or not rubles(lang):
            return
        if user.email:
            await send_payment_link(cb.message, bot, user, user.email, days)
            return
        await storage.update(user.user_id, awaiting_email=days)
        await cb.message.answer(t(lang, "ask_email"))

    @router.callback_query(F.data == "yk:check")
    async def on_yk_check(cb: CallbackQuery, bot: Bot) -> None:
        _, lang = await load(cb.from_user)
        try:
            paid = await billing.check_user(cb.from_user.id)
        except YooKassaError as e:
            log.warning("payment check for %s failed: %s", cb.from_user.id, e)
            paid = False
        if paid:
            await cb.answer(t(lang, "paid_ok"))
            await cb.message.edit_reply_markup(reply_markup=None)
        else:
            user = await storage.user(cb.from_user.id)
            await cb.answer(t(lang, "already_active" if companion.subscribed(user) else "not_paid_yet"),
                            show_alert=True)

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        user, lang = await load(message.from_user)
        await message.answer(
            t(lang, "settings", name=companion.name(user), from_h=cfg.checkin_from_hour, to_h=cfg.checkin_to_hour),
            reply_markup=_checkins_kb(lang, bool(user.checkins)),
        )

    @router.callback_query(F.data.startswith("checkins:"))
    async def on_checkins(cb: CallbackQuery) -> None:
        _, lang = await load(cb.from_user)
        on = cb.data.endswith(":on")
        await storage.update(cb.from_user.id, checkins=int(on))
        await cb.message.edit_reply_markup(reply_markup=_checkins_kb(lang, on))
        await cb.answer(t(lang, "toggled_on" if on else "toggled_off"))

    @router.message(Command("forget"))
    async def cmd_forget(message: Message) -> None:
        _, lang = await load(message.from_user)
        await message.answer(
            t(lang, "forget_confirm"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t(lang, "btn_yes_erase"), callback_data="forget:yes"),
                InlineKeyboardButton(text=t(lang, "btn_no"), callback_data="forget:no"),
            ]]),
        )

    @router.callback_query(F.data.startswith("forget:"))
    async def on_forget(cb: CallbackQuery) -> None:
        _, lang = await load(cb.from_user)
        if cb.data.endswith(":yes"):
            async with companion.locks[cb.from_user.id]:
                await storage.forget_everything(cb.from_user.id)
            await cb.message.edit_text(t(lang, "forgot"))
        else:
            await cb.message.edit_text(t(lang, "kept"))
        await cb.answer()

    @router.message(Command("paysupport"))
    async def cmd_paysupport(message: Message) -> None:
        _, lang = await load(message.from_user)
        contact = t(lang, "paysupport_contact", contact=cfg.support) if cfg.support else ""
        await message.answer(t(lang, "paysupport") + contact)

    @router.message(Command("terms"))
    async def cmd_terms(message: Message) -> None:
        user, lang = await load(message.from_user)
        await message.answer(t(lang, "terms", name=companion.name(user), prices=prices(user, lang)))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        user, lang = await load(message.from_user)
        await message.answer(t(lang, "help", name=companion.name(user)))

    @router.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        if message.from_user.id not in cfg.admin_ids:
            return
        s = await storage.admin_stats(int(time.time()))
        spent = cost(cfg.model, s["inp"], s["out"], s["cached"])
        langs = ", ".join(f"{code} {n}" for code, n in s["langs"])
        await message.answer(
            f"👥 Пользователей: {s['users']} · активных за 7 дней: {s['active']}\n"
            f"🌍 Языки: {langs or '—'}\n"
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
        user, lang = await load(message.from_user)
        if not await storage.record_payment(p.telegram_payment_charge_id, user.user_id, p.total_amount,
                                            bool(p.is_recurring)):
            return  # duplicate delivery
        days = 14 if p.invoice_payload == "plan14" else 30
        if p.subscription_expiration_date:
            # Never shorten time already paid another way (e.g. through YooKassa).
            until = max(int(p.subscription_expiration_date), user.paid_until)
            await storage.update(user.user_id, paid_until=until, plan_days=days)
        else:
            await storage.extend(user.user_id, days, int(time.time()))
        await storage.update(user.user_id, charge_id=p.telegram_payment_charge_id)
        if not p.is_recurring or p.is_first_recurring:
            await message.answer(t(lang, "thanks_paid"))

    @router.message(F.voice | F.video_note | F.audio)
    async def on_voice(message: Message) -> None:
        _, lang = await load(message.from_user)
        await message.answer(t(lang, "voice"))

    @router.message(F.text | F.photo | F.sticker | F.document)
    async def on_message(message: Message, bot: Bot) -> None:
        user, lang = await load(message.from_user)
        uid = user.user_id
        if not allowed(uid):
            await message.answer(t(lang, "closed"))
            return
        if user.awaiting_email:
            await storage.update(uid, awaiting_email=0)
            text = (message.text or "").strip()
            if EMAIL.match(text) and user.awaiting_email in cfg.plans():
                await storage.update(uid, email=text)
                await send_payment_link(message, bot, user, text, user.awaiting_email)
                return
            # Not an email: they changed their mind, so treat it as a normal message.
        blocked = await companion.admit(user)
        if blocked:
            key, fields = blocked
            if key == "paywall":
                await show_plans(message, bot, message.from_user, "paywall")
            else:
                await message.answer(t(lang, key, **fields))
            return
        try:
            content = await _content(message, bot)
        except UserFacingError as e:
            await message.answer(t(lang, str(e)))
            return
        await storage.user_wrote(uid, int(time.time()), counted=not companion.subscribed(user))
        try:
            await companion.converse(uid, content)
        except anthropic.RateLimitError:
            await message.answer(t(lang, "busy"))
        except anthropic.APIError:
            log.exception("Claude API error for %s", uid)
            await message.answer(t(lang, "api_error"))

    return router


def _checkins_kb(lang: str, on: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=("✅ " if on else "") + t(lang, "btn_checkins_on"), callback_data="checkins:on"),
        InlineKeyboardButton(text=("✅ " if not on else "") + t(lang, "btn_checkins_off"), callback_data="checkins:off"),
    ]])


async def _content(message: Message, bot: Bot) -> list[dict]:
    blocks: list[dict] = []
    if message.photo:
        photo = message.photo[-1]
        blocks.append(_image(await _download(bot, photo.file_id, photo.file_size), "image/jpeg"))
    elif message.document:
        doc = message.document
        if doc.mime_type not in IMAGE_TYPES:
            raise UserFacingError("bad_file")
        blocks.append(_image(await _download(bot, doc.file_id, doc.file_size), doc.mime_type))
    elif message.sticker:
        blocks.append({"type": "text", "text": f"[sent a sticker {message.sticker.emoji or ''}]"})

    text = message.text or message.caption
    if message.forward_origin:
        text = f"[forwarded message]\n{text or ''}"
    if text:
        blocks.append({"type": "text", "text": text})
    elif message.photo or message.document:
        blocks.append({"type": "text", "text": "[sent a photo without a caption]"})
    return blocks


async def _download(bot: Bot, file_id: str, size: int | None) -> bytes:
    if size and size > MAX_FILE:
        raise UserFacingError("big_file")
    return (await bot.download(file_id)).read()


def _image(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode()}}
