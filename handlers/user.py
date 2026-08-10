from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from database import (
    async_session, User, ServiceOrder, BotContent, ServicePlan, Panel,
    WalletTransaction, Referral, MenuButton,
)
from keyboards.user_kb import main_keyboard, wallet_menu_keyboard
from ui_texts import ButtonText
from panels import renew_panel_account
from middleware import get_required_channel, is_member
import config as cfg

router = Router()


async def get_content(session, key, default="", **variables):
    from ui_texts import get_managed_text
    return await get_managed_text(key, default, **variables)


async def register_user_if_needed(bot, user_id: int, username: str, full_name: str, ref_payload: str = None):
    """
    ثبت کاربر توی دیتابیس اگه قبلاً وجود نداشته (هم موقع /start هم موقع تایید عضویت کانال
    صدا زده میشه تا کاربری که اول باید عضو کانال بشه، بعد از زدن «عضو شدم» هم درست ثبت بشه
    و مجبور نباشه دوباره /start بزنه).
    برمی‌گردونه: (is_new_user: bool)
    """
    inviter_id = None
    if ref_payload and ref_payload.replace("ref_", "").isdigit():
        candidate = int(ref_payload.replace("ref_", ""))
        if candidate != user_id:
            inviter_id = candidate

    is_new_user = False
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == user_id))
        if not user:
            is_new_user = True
            user = User(user_id=user_id, username=username, full_name=full_name, balance=0,
                        referred_by=inviter_id if inviter_id else None)
            session.add(user)
            await session.commit()

            if inviter_id:
                inviter = await session.scalar(select(User).where(User.user_id == inviter_id))
                if inviter:
                    session.add(Referral(inviter_id=inviter_id, invited_id=user_id))
                    inviter.referral_count += 1
                    reward_ready = inviter.referral_count % cfg.REFERRAL_NEEDED_COUNT == 0
                    if reward_ready:
                        inviter.balance += cfg.REFERRAL_BONUS_AMOUNT
                        session.add(WalletTransaction(
                            user_id=inviter_id, amount=cfg.REFERRAL_BONUS_AMOUNT,
                            transaction_type="BONUS", method="ADMIN", status="SUCCESS",
                            description="پاداش دعوت دوستان",
                        ))
                    await session.commit()
                    if reward_ready:
                        try:
                            await bot.send_message(
                                inviter_id,
                                f"🎉 تبریک! به خاطر دعوت {cfg.REFERRAL_NEEDED_COUNT} نفر، "
                                f"{cfg.REFERRAL_BONUS_AMOUNT:,} تومان به کیف پول شما اضافه شد."
                            )
                        except Exception:
                            pass

        if is_new_user:
            uname = f"@{username}" if username else "بدون یوزرنیم"
            ref_note = f"\nاز طریق لینک دعوت کاربر: {inviter_id}" if inviter_id else ""
            for admin_id in cfg.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 کاربر جدید وارد ربات شد\n\n"
                        f"🆔 آیدی عددی: {user_id}\n"
                        f"👤 نام: {full_name}\n"
                        f"✏️ یوزرنیم: {uname}{ref_note}",
                    )
                except Exception:
                    pass

    return is_new_user


# ==================== عضویت اجباری کانال ====================

@router.callback_query(F.data == "check_membership")
async def check_membership_cb(callback: CallbackQuery):
    channel = await get_required_channel()
    if channel and not await is_member(callback.bot, channel, callback.from_user.id):
        await callback.answer("هنوز عضو کانال نشدید ❌", show_alert=True)
        return

    # ✅ همینجا هم کاربر رو ثبت می‌کنیم، دیگه نیازی به زدن دوباره‌ی /start نیست
    await register_user_if_needed(
        callback.bot, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )

    await callback.answer("✅ عضویت تایید شد!")
    await callback.message.answer("🎉 خوش اومدید! از منوی زیر انتخاب کنید:", reply_markup=await main_keyboard())


# ==================== شروع + رفرال ====================

@router.message(F.text.startswith("/start"))
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    parts = message.text.split(maxsplit=1)
    ref_payload = parts[1].strip() if len(parts) > 1 else None

    await register_user_if_needed(message.bot, user_id, username, full_name, ref_payload)

    from ui_texts import get_managed_text_and_entities
    welcome_text, welcome_entities = await get_managed_text_and_entities("welcome", "👋 خوش آمدید!", name=full_name)
    await message.answer(welcome_text, entities=welcome_entities, reply_markup=await main_keyboard())


# ==================== پروفایل من ====================

@router.message(ButtonText("btn_profile"))
async def profile_handler(message: Message):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == message.from_user.id))
        if not user:
            await message.answer("❌ اطلاعات حساب کاربری شما یافت نشد. لطفاً /start را بزنید.")
            return

    tg_user = message.from_user
    name = tg_user.full_name or "ثبت نشده"
    username = f"@{tg_user.username}" if tg_user.username else "ندارد"

    text = (
        f"👤 اطلاعات حساب\n\n"
        f"ℹ️ نام: {name}\n"
        f"✏️ یوزرنیم: {username}\n"
        f"👤 آیدی عددی: {user.user_id}\n"
        f"👛 موجودی کیف پول: {user.balance:,} تومان\n\n"
        f"▪️ تعداد خریدها: {user.total_purchases}\n"
        f"📦 حجم خریداری‌شده: {user.total_volume} گیگ\n"
        f"♾ خریدهای نامحدود: {user.total_unlimited_purchases} عدد\n"
        f"🪙 مبلغ کل خریدها: {user.total_spent:,.0f} تومان\n"
        f"👥 ثبت‌نام با لینک دعوت شما: {user.referral_count} نفر\n\n"
        f"✔️ برای افزایش موجودی در بخش کیف پول اقدام کنید."
    )
    await message.answer(text, reply_markup=await main_keyboard())


# ==================== تعرفه‌ها ====================

@router.message(ButtonText("btn_tariffs"))
async def tariffs_handler(message: Message):
    from ui_texts import get_managed_text_and_entities
    text, entities = await get_managed_text_and_entities("tariffs", "تعرفه‌ای ثبت نشده.")
    await message.answer(text, entities=entities, reply_markup=await main_keyboard())


# ==================== دعوت دوستان ====================

@router.message(ButtonText("btn_invite"))
async def referral_handler(message: Message):
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    text = (
        f"👥 برنامه دعوت از دوستان\n\n"
        f"با اشتراک‌گذاری لینک زیر، وقتی هر {cfg.REFERRAL_NEEDED_COUNT} نفر با لینک شما وارد ربات بشن، "
        f"{cfg.REFERRAL_BONUS_AMOUNT:,} تومان به کیف پول شما اضافه می‌شود.\n\n"
        f"🔗 لینک اختصاصی شما:\n`{ref_link}`"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=await main_keyboard())


# ==================== آموزش اتصال ====================

@router.message(ButtonText("btn_guide"))
async def guide_handler(message: Message):
    from ui_texts import get_managed_text_and_entities
    text, entities = await get_managed_text_and_entities("guide", "آموزشی ثبت نشده.")
    await message.answer(text, entities=entities, reply_markup=await main_keyboard())


# ==================== پشتیبانی ====================

@router.message(ButtonText("btn_support"))
async def support_handler(message: Message):
    async with async_session() as session:
        sup_id = await get_content(session, "support_id", "@your_support_username")
    await message.answer(f"📞 پشتیبانی Lidso\n\nجهت ارتباط با اپراتور به آیدی زیر پیام دهید:\n🆔 {sup_id}",
                          reply_markup=await main_keyboard())


# ==================== سرویس‌های من (inline) ====================

@router.message(ButtonText("btn_my_services"))
async def my_services(message: Message):
    async with async_session() as session:
        orders = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.user_id == message.from_user.id,
                ServiceOrder.status.in_(["ACTIVE", "PENDING_MANUAL"]),
            ).order_by(ServiceOrder.id.desc())
        )).scalars().all()

    if not orders:
        await message.answer("⚠️ شما هنوز هیچ سرویس فعالی خریداری نکرده‌اید.", reply_markup=await main_keyboard())
        return

    rows = []
    for o in orders:
        prefix_icon = "🎁 " if o.is_trial else "🔑 "
        label = o.config_name if o.status == "ACTIVE" else f"{o.service_name} (در حال آماده‌سازی)"
        rows.append([InlineKeyboardButton(text=f"{prefix_icon}{label}", callback_data=f"myservice_{o.id}")])

    await message.answer(
        "📦 لیست سرویس‌های شما (برای مشاهده جزئیات کلیک کنید):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("myservice_"))
async def my_service_detail(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        order = await session.get(ServiceOrder, order_id)

    if not order or order.user_id != callback.from_user.id:
        await callback.answer("پیدا نشد.", show_alert=True)
        return

    if order.status == "PENDING_MANUAL":
        text = f"📦 سرویس: {order.service_name}\n⏳ در حال آماده‌سازی توسط پشتیبانی است."
        await callback.message.answer(text, parse_mode="Markdown")
    else:
        expire_str = order.expire_at.strftime("%Y-%m-%d") if order.expire_at else "-"
        caption = (
            f"📦 سرویس: {order.service_name}\n"
            f"🔑 نام کانفیگ: `{order.config_name}`\n"
            f"📅 تاریخ انقضا: {expire_str}\n\n"
            f"🔗 لینک اتصال:\n`{order.config_link}`"
        )
        try:
            from qr_utils import generate_qr_photo
            await callback.message.answer_photo(
                generate_qr_photo(order.config_link, filename=f"{order.config_name}.png"),
                caption=caption,
                parse_mode="Markdown",
            )
        except Exception:
            await callback.message.answer(caption, parse_mode="Markdown")
    await callback.answer()


# ==================== تمدید اشتراک ====================

@router.message(ButtonText("btn_renew"))
async def renew_subscription(message: Message):
    async with async_session() as session:
        orders = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.user_id == message.from_user.id,
                ServiceOrder.status == "ACTIVE",
                ServiceOrder.is_trial == False,
            ).order_by(ServiceOrder.id.desc())
        )).scalars().all()

    if not orders:
        await message.answer(
            "🔄 شما در حال حاضر سرویس فعالی برای تمدید ندارید.\n"
            "برای خرید سرویس جدید از منوی «خرید سرویس» اقدام کنید.",
            reply_markup=await main_keyboard(),
        )
        return

    rows = [[InlineKeyboardButton(text=f"🔄 {o.config_name}", callback_data=f"renew_{o.id}")] for o in orders]
    await message.answer("🔄 کدام سرویس را می‌خواهید تمدید کنید؟",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("renew_"))
async def do_renew(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    async with async_session() as session:
        order = await session.get(ServiceOrder, order_id)
        if not order or order.user_id != user_id:
            await callback.answer("سرویس پیدا نشد.", show_alert=True)
            return
        if order.is_trial:
            await callback.answer("❌ سرویس‌های تست رایگان قابل تمدید نیستن.", show_alert=True)
            return

        plan = await session.get(ServicePlan, order.plan_id) if order.plan_id else None
        price = plan.price if plan else order.price
        days = plan.duration_days if plan else 30

        user = await session.scalar(select(User).where(User.user_id == user_id))
        if user.balance < price:
            await callback.message.answer(
                f"❌ موجودی کیف پول کافی نیست.\n💰 موجودی: {user.balance:,} تومان\n"
                f"💵 مبلغ مورد نیاز: {price:,} تومان",
                reply_markup=await wallet_menu_keyboard(),
            )
            await callback.answer()
            return

        user.balance -= price
        session.add(WalletTransaction(
            user_id=user_id, amount=price, transaction_type="RENEWAL",
            method="WALLET", status="SUCCESS", description=f"تمدید {order.config_name}",
        ))

        panel_note = ""
        if order.panel_id and plan:
            panel = await session.get(Panel, order.panel_id)
            if panel:
                try:
                    await renew_panel_account(panel, order.config_name, plan)
                except Exception as e:
                    panel_note = "\n⚠️ تمدید در پنل با خطا مواجه شد، پشتیبانی مطلع شد."
                    for admin_id in cfg.ADMIN_IDS:
                        try:
                            await callback.bot.send_message(
                                admin_id,
                                f"🔴 خطا در تمدید خودکار\n\nپنل: {panel.name}\n"
                                f"کانفیگ: {order.config_name}\nکاربر: {user_id}\nخطا: {e}"
                            )
                        except Exception:
                            pass

        order.expire_at = None if days == 0 else datetime.now(timezone.utc) + timedelta(days=days)
        order.renew_notified = False
        order.status = "ACTIVE"
        await session.commit()

    await callback.message.answer(
        f"✅ سرویس «{order.config_name}» با موفقیت تمدید شد.{panel_note}",
        reply_markup=await main_keyboard(),
    )
    await callback.answer()


# ==================== دکمه‌های سفارشی منوی اصلی (اضافه‌شده از /admin) ====================

class CustomMenuButtonText(BaseFilter):
    async def __call__(self, message: Message):
        if not message.text:
            return False
        async with async_session() as session:
            btn = await session.scalar(
                select(MenuButton).where(
                    MenuButton.is_custom == True,
                    MenuButton.enabled == True,
                    MenuButton.label == message.text,
                )
            )
        if btn:
            return {"custom_button": btn}
        return False


@router.message(CustomMenuButtonText())
async def custom_menu_button_handler(message: Message, custom_button):
    await message.answer(custom_button.response_text or "—", reply_markup=await main_keyboard())


# ==================== بازگشت به منوی اصلی ====================

@router.message(ButtonText("btn_back_main"))
async def back_to_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("به منوی اصلی برگشتید. لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
                          reply_markup=await main_keyboard())
