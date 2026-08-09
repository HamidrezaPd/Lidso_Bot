from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from database import async_session, User, DiscountCode, UsedDiscount, WalletTransaction, BotContent
from ui_texts import ButtonText
from keyboards.user_kb import (
    wallet_menu_keyboard, request_phone_keyboard, main_keyboard,
    back_to_wallet_keyboard,
)
import config as cfg

router = Router()

CARD_PAYMENT_MINUTES = 20
CRYPTO_PAYMENT_MINUTES = 20


class WalletStates(StatesGroup):
    waiting_for_promo = State()
    waiting_card_amount = State()
    waiting_card_receipt = State()
    waiting_crypto_amount = State()
    waiting_crypto_receipt = State()
    waiting_gateway_amount = State()


async def _notify_admins(bot, text):
    for admin_id in cfg.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


METHOD_ENABLE_KEYS = {
    "CARD": "payment_method_card_enabled",
    "GATEWAY": "payment_method_gateway_enabled",
    "CRYPTO": "payment_method_crypto_enabled",
}


async def is_payment_method_enabled(method: str) -> bool:
    key = METHOD_ENABLE_KEYS.get(method)
    if not key:
        return True
    async with async_session() as session:
        val = await session.scalar(select(BotContent.value).where(BotContent.key == key))
    return (val or "1") != "0"  # پیش‌فرض فعال - یعنی رفتار قبلی حفظ میشه


METHOD_LIMIT_KEYS = {
    "CARD": ("min_topup_card", "max_topup_card"),
    "GATEWAY": ("min_topup_gateway", "max_topup_gateway"),
    "CRYPTO": ("min_topup_crypto", "max_topup_crypto"),
}


async def get_min_topup(method: str = "CARD"):
    """حداقل مبلغ شارژ مخصوص هر روش پرداخت (کارت/درگاه/کریپتو) - اگه ست نشده بود، به مقدار عمومی قدیمی برمی‌گرده."""
    min_key, _ = METHOD_LIMIT_KEYS.get(method, METHOD_LIMIT_KEYS["CARD"])
    async with async_session() as session:
        val = await session.scalar(select(BotContent.value).where(BotContent.key == min_key))
        if val is None:
            val = await session.scalar(select(BotContent.value).where(BotContent.key == "min_topup_amount"))
    try:
        return int(val)
    except (TypeError, ValueError):
        return cfg.MIN_TOPUP_AMOUNT


async def get_max_topup(method: str = "CARD"):
    """حداکثر مبلغ شارژ مخصوص هر روش پرداخت. اگه ست نشده باشه یا صفر باشه یعنی سقفی نداره."""
    _, max_key = METHOD_LIMIT_KEYS.get(method, METHOD_LIMIT_KEYS["CARD"])
    async with async_session() as session:
        val = await session.scalar(select(BotContent.value).where(BotContent.key == max_key))
    try:
        v = int(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


async def validate_topup_amount(text: str, method: str):
    """
    مبلغ وارد شده رو با حداقل/حداکثر مخصوص همون روش پرداخت چک می‌کنه.
    خروجی: (amount یا None, پیام خطا یا None)
    """
    clean = (text or "").replace(",", "").strip()
    min_topup = await get_min_topup(method)
    max_topup = await get_max_topup(method)

    if not clean.isdigit():
        return None, f"❌ مبلغ نامعتبر است. حداقل {min_topup:,} تومان وارد کنید:"

    amount = int(clean)
    if amount < min_topup:
        return None, f"❌ مبلغ نامعتبر است. حداقل {min_topup:,} تومان وارد کنید:"
    if max_topup and amount > max_topup:
        return None, f"❌ مبلغ نامعتبر است. حداکثر {max_topup:,} تومان مجاز است. مبلغ کمتری وارد کنید:"

    return amount, None


async def get_content(session, key, default=""):
    val = await session.scalar(select(BotContent.value).where(BotContent.key == key))
    return val or default


def _not_expired(tx) -> bool:
    return bool(tx.expires_at) and datetime.now(timezone.utc) <= tx.expires_at.replace(tzinfo=timezone.utc)


async def get_open_tx(session, user_id):
    """آخرین تراکنش هنوز باز (منتظر رسید یا منتظر تایید ادمین)"""
    return await session.scalar(
        select(WalletTransaction).where(
            WalletTransaction.user_id == user_id,
            WalletTransaction.status.in_(["AWAITING_RECEIPT", "PENDING", "AWAITING_GATEWAY", "AWAITING_CRYPTO"]),
        ).order_by(WalletTransaction.id.desc())
    )


def resume_or_cancel_kb(tx_id: int, can_resume: bool):
    rows = []
    if can_resume:
        rows.append([InlineKeyboardButton(text="▶️ ادامه همین پرداخت", callback_data=f"payresume_{tx_id}")])
    rows.append([InlineKeyboardButton(text="❌ لغو و شروع پرداخت جدید", callback_data=f"paycancel_{tx_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==================== منوی کیف پول ====================

@router.message(ButtonText("btn_wallet"))
async def wallet_main(message: Message, state: FSMContext):
    await state.clear()
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == message.from_user.id))
        bal = user.balance if user else 0
    await message.answer(
        f"💳 کیف پول\n\n💰 موجودی فعلی: {bal:,} تومان",
        reply_markup=await wallet_menu_keyboard(),
    )


@router.message(ButtonText("btn_back"))
async def wallet_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("به منوی کیف پول بازگشتید:", reply_markup=await wallet_menu_keyboard())


# ==================== چک کردن پرداخت باز، قبل از هر شارژ جدید ====================

GATEWAY_PAYMENT_MINUTES = 20


async def _check_open_or_continue(message: Message, state: FSMContext) -> bool:
    """
    اگه یه تراکنش باز وجود داشته باشه، رفتار بر اساس نوعش فرق می‌کنه:
    - AWAITING_RECEIPT (کارت به کارت، منتظر ارسال عکس رسید): گزینه‌ی ادامه یا لغو
    - PENDING (رسید کارت به کارت ارسال شده، منتظر تایید دستی ادمین): فقط لغو یا صبر
    - AWAITING_GATEWAY / AWAITING_CRYPTO (درگاه/کریپتو - خودکار با API چک میشه، کاربر رسیدی نمی‌فرسته):
      این‌ها منتظر «تایید ادمین» نیستن! یا خودکار توسط سیستم چک/تایید میشن، یا با گذشت مهلت زمانی
      (۲۰ دقیقه) خودکار EXPIRED میشن. برای همین اصلاً پیام «منتظر ادمین» بهشون نمیدیم -
      یا مهلتشون تموم شده و اجازه‌ی پرداخت جدید میدیم، یا هنوز توی مهلتن و بهشون میگیم چطور
      ادامه بدن (دکمه‌ی بررسی پرداخت) یا لغو کنن.
    True یعنی ادامه بده (پرداخت بازی نیست یا منقضی شده). False یعنی صبر کن، کاربر باید انتخاب کنه.
    """
    async with async_session() as session:
        tx = await get_open_tx(session, message.from_user.id)
        if not tx:
            return True

        # اگه مهلت زمانی تموم شده (چه AWAITING_RECEIPT چه AWAITING_GATEWAY چه AWAITING_CRYPTO)
        # خودکار منقضی کن و اجازه بده پرداخت جدید شروع بشه - بدون هیچ سوالی
        if tx.status in ("AWAITING_RECEIPT", "AWAITING_GATEWAY", "AWAITING_CRYPTO") and not _not_expired(tx):
            tx.status = "EXPIRED"
            await session.commit()
            return True

        method_fa = {"CARD": "کارت به کارت", "GATEWAY": "درگاه پرداخت", "TON": "پرداخت ارزی (TON)",
                     "USDC": "پرداخت ارزی (USDC)"}.get(tx.method, tx.method)
        tx_id, tx_amount, tx_method, tx_status = tx.id, tx.amount, tx.method, tx.status

    if tx_status == "PENDING":
        # رسید کارت به کارت ارسال شده و واقعاً منتظر بررسی دستی ادمینه
        await message.answer(
            f"⚠️ شما یک پرداخت باز دارید:\n\n"
            f"روش: {method_fa}\nمبلغ: {tx_amount:,} تومان\nوضعیت: رسید ارسال شده، در انتظار تایید ادمین\n\n"
            "این پرداخت رسیدش قبلاً ارسال شده و منتظر بررسی ادمینه.\n"
            "می‌تونی صبر کنی تا ادمین تاییدش کنه، یا اگه منصرف شدی «لغو» رو بزن تا این پرداخت به‌طور کامل کنسل بشه "
            "و بتونی یه پرداخت کاملاً جدید (با روش یا مبلغ دیگه) شروع کنی.",
            reply_markup=resume_or_cancel_kb(tx_id, can_resume=False),
        )
        return False

    if tx_status == "AWAITING_RECEIPT":
        await message.answer(
            f"⚠️ شما یک پرداخت باز دارید:\n\n"
            f"روش: {method_fa}\nمبلغ: {tx_amount:,} تومان\nوضعیت: در انتظار ارسال رسید\n\n"
            "می‌خوای همین رو ادامه بدی یا لغوش کنی و پرداخت جدید (با روش/مبلغ دیگه) شروع کنی؟\n\n"
            "❗️ اگه «لغو» رو بزنی، این پرداخت به‌طور کامل منصرف‌شده در نظر گرفته میشه و باید از اول یه پرداخت جدید انجام بدی.",
            reply_markup=resume_or_cancel_kb(tx_id, can_resume=True),
        )
        return False

    # AWAITING_GATEWAY یا AWAITING_CRYPTO: هیچ ادمینی درگیر نیست، فقط منتظر تکمیل پرداخت خودتی
    check_cb = f"gwcheck_{tx_id}" if tx_status == "AWAITING_GATEWAY" else f"cryptocheck_{tx_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 بررسی پرداخت", callback_data=check_cb)],
        [InlineKeyboardButton(text="❌ لغو و شروع پرداخت جدید", callback_data=f"paycancel_{tx_id}")],
    ])
    await message.answer(
        f"⚠️ شما یک پرداخت ناتمام دارید:\n\n"
        f"روش: {method_fa}\nمبلغ: {tx_amount:,} تومان\nوضعیت: در انتظار تکمیل پرداخت شما\n\n"
        "این پرداخت هنوز کامل نشده و منتظر تایید ادمین نیست - به محض واریز، خودکار بررسی و تایید میشه.\n"
        "اگه پرداختت رو انجام دادی، «بررسی پرداخت» رو بزن. اگه منصرف شدی، «لغو» رو بزن تا بتونی "
        "با روش یا مبلغ دیگه‌ای از نو اقدام کنی.",
        reply_markup=kb,
    )
    return False


@router.callback_query(F.data.startswith("paycancel_"))
async def pay_cancel(callback: CallbackQuery, state: FSMContext):
    tx_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if tx and tx.status in ("AWAITING_RECEIPT", "PENDING", "AWAITING_GATEWAY", "AWAITING_CRYPTO"):
            tx.status = "CANCELLED"
            await session.commit()
    await state.clear()
    await callback.answer("لغو شد ✅")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "❌ پرداخت قبلی به‌طور کامل لغو شد و منصرف در نظر گرفته شد.\n"
        "می‌تونی از منوی کیف پول یه پرداخت کاملاً جدید (با هر روش و مبلغی که بخوای) شروع کنی:",
        reply_markup=await wallet_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("payresume_"))
async def pay_resume(callback: CallbackQuery, state: FSMContext):
    tx_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "AWAITING_RECEIPT" or not _not_expired(tx):
            await callback.answer("این پرداخت دیگه معتبر نیست.", show_alert=True)
            return
        card_number = await get_content(session, "card_number", "-")
        card_holder = await get_content(session, "card_holder", "-")
        crypto_address = await get_content(session, "crypto_address", "-")

    await state.update_data(tx_id=tx.id, amount=tx.amount)
    await callback.answer()

    if tx.method == "CARD":
        await callback.message.answer(
            f"💳 شماره کارت جهت واریز:\n\n`{card_number}`\nبه نام: {card_holder}\n\n"
            f"مبلغ {tx.amount:,} تومان را واریز کرده و عکس رسید را همینجا ارسال کنید 📸",
            parse_mode="Markdown",
            reply_markup=await back_to_wallet_keyboard(),
        )
        await state.set_state(WalletStates.waiting_card_receipt)
    else:
        await callback.message.answer(
            f"🪙 آدرس ولت (روی متن زیر بزنید تا کپی بشه):\n<code>{crypto_address}</code>\n\n"
            f"پس از واریز {tx.amount:,} تومان معادل، عکس رسید یا TxID را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=await back_to_wallet_keyboard(),
        )
        await state.set_state(WalletStates.waiting_crypto_receipt)


# ==================== کارت به کارت ====================

@router.message(ButtonText("btn_wallet_card"))
async def card_pay_start(message: Message, state: FSMContext):
    if not await is_payment_method_enabled("CARD"):
        await message.answer("⚠️ روش پرداخت کارت به کارت فعلاً غیرفعاله. لطفاً از روش دیگه‌ای استفاده کن.",
                              reply_markup=await wallet_menu_keyboard())
        return
    if not await _check_open_or_continue(message, state):
        return

    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == message.from_user.id))
        require_phone_val = await session.scalar(
            select(BotContent.value).where(BotContent.key == "require_phone_for_card")
        )
        # پیش‌فرض "1" (فعال) - یعنی رفتار قبلی حفظ میشه مگه اینکه ادمین از پنل غیرفعالش کنه
        require_phone = (require_phone_val or "1") == "1"

        if require_phone and (not user or not user.phone_number):
            await message.answer(
                "⚠️ برای مشاهده شماره کارت، ابتدا شماره تلفن خود را ارسال کنید:\n"
                "(یا با دکمه بازگشت به بقیه روش‌های پرداخت برگردید)",
                reply_markup=await request_phone_keyboard(),
            )
            return

    min_topup = await get_min_topup("CARD")
    max_topup = await get_max_topup("CARD")
    max_line = f"\n(حداکثر مبلغ: {max_topup:,} تومان)" if max_topup else ""
    await message.answer(
        f"💳 لطفاً مبلغی که می‌خواهید واریز کنید را به تومان وارد کنید:\n"
        f"(حداقل مبلغ: {min_topup:,} تومان){max_line}",
        reply_markup=await back_to_wallet_keyboard(),
    )
    await state.set_state(WalletStates.waiting_card_amount)


@router.message(F.contact)
async def get_contact(message: Message):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == message.from_user.id))
        if user:
            user.phone_number = message.contact.phone_number
            await session.commit()

    for admin_id in cfg.ADMIN_IDS:
        try:
            uname = f"@{message.from_user.username}" if message.from_user.username else "بدون یوزرنیم"
            await message.bot.send_message(
                admin_id,
                f"📱 شماره تلفن جدید ثبت شد\n\n"
                f"🆔 کاربر: {message.from_user.id}\n"
                f"👤 نام: {message.from_user.full_name}\n"
                f"✏️ یوزرنیم: {uname}\n"
                f"☎️ شماره: {message.contact.phone_number}",
            )
        except Exception:
            pass

    await message.answer(
        "✅ شماره تلفن شما ثبت شد. اکنون می‌توانید «کارت به کارت» را دوباره انتخاب کنید.",
        reply_markup=await wallet_menu_keyboard(),
    )


@router.message(WalletStates.waiting_card_amount)
async def card_pay_amount(message: Message, state: FSMContext):
    amount, err = await validate_topup_amount(message.text, "CARD")
    if err:
        await message.answer(err)
        return

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CARD_PAYMENT_MINUTES)

    async with async_session() as session:
        tx = WalletTransaction(
            user_id=message.from_user.id, amount=amount, transaction_type="DEPOSIT",
            method="CARD", status="AWAITING_RECEIPT", expires_at=expires_at,
            description="در انتظار ارسال رسید کارت به کارت",
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)
        card_number = await get_content(session, "card_number", "شماره کارت تنظیم نشده")
        card_holder = await get_content(session, "card_holder", "-")

    await state.update_data(tx_id=tx.id, amount=amount)
    await message.answer(
        f"💳 شماره کارت جهت واریز:\n\n`{card_number}`\nبه نام: {card_holder}\n\n"
        f"مبلغ {amount:,} تومان را واریز کرده و سپس عکس رسید را همینجا ارسال کنید 📸\n\n"
        f"⏳ مهلت شما برای ارسال رسید: {CARD_PAYMENT_MINUTES} دقیقه",
        parse_mode="Markdown",
        reply_markup=await back_to_wallet_keyboard(),
    )
    await state.set_state(WalletStates.waiting_card_receipt)


@router.message(WalletStates.waiting_card_receipt, F.photo)
async def card_pay_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    tx_id = data.get("tx_id")
    amount = data.get("amount")
    user_id = message.from_user.id
    photo_id = message.photo[-1].file_id

    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id) if tx_id else None
        if not tx or tx.status != "AWAITING_RECEIPT" or not _not_expired(tx):
            await message.answer(
                f"⏳ مهلت {CARD_PAYMENT_MINUTES} دقیقه‌ای این پرداخت تموم شده یا نامعتبره. "
                f"لطفاً دوباره از «کارت به کارت» شروع کنید.",
                reply_markup=await wallet_menu_keyboard(),
            )
            await state.clear()
            return

        tx.status = "PENDING"
        tx.receipt_file_id = photo_id
        tx.description = "واریز کارت به کارت - در انتظار تایید"
        await session.commit()

    await message.answer(
        "✅ رسید شما ثبت شد و پس از تایید ادمین، کیف پول شما شارژ می‌شود.",
        reply_markup=await main_keyboard(),
    )

    for admin_id in cfg.ADMIN_IDS:
        try:
            from keyboards.admin_kb import tx_approve_kb
            await message.bot.send_photo(
                admin_id, photo_id,
                caption=(
                    f"🧾 رسید واریز جدید\n\n"
                    f"شماره تراکنش: #{tx_id}\n"
                    f"کاربر: {user_id}\n"
                    f"مبلغ: {amount:,} تومان"
                ),
                reply_markup=tx_approve_kb(tx_id),
            )
        except Exception:
            pass

    await state.clear()


@router.message(WalletStates.waiting_card_receipt)
async def card_pay_receipt_wrong_type(message: Message):
    await message.answer("لطفاً عکس رسید پرداخت را ارسال کنید 📸")


# ==================== پرداخت ارزی (گرام روی TON / USDC روی BEP20) ====================

@router.message(ButtonText("btn_wallet_crypto"))
async def crypto_pay_start(message: Message, state: FSMContext):
    if not await is_payment_method_enabled("CRYPTO"):
        await message.answer("⚠️ روش پرداخت ارزی فعلاً غیرفعاله. لطفاً از روش دیگه‌ای استفاده کن.",
                              reply_markup=await wallet_menu_keyboard())
        return
    if not await _check_open_or_continue(message, state):
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 پرداخت با گرام (TON)", callback_data="cryptomethod_TON")],
        [InlineKeyboardButton(text="💵 پرداخت با USDC (BEP20)", callback_data="cryptomethod_BSC")],
    ])
    await message.answer("🪙 روش پرداخت ارزی رو انتخاب کن:", reply_markup=kb)


@router.callback_query(F.data.startswith("cryptomethod_"))
async def crypto_method_pick(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split("_")[1]  # TON یا BSC
    await state.update_data(crypto_method=method)
    method_fa = "گرام (TON)" if method == "TON" else "USDC (BEP20)"
    min_topup = await get_min_topup("CRYPTO")
    max_topup = await get_max_topup("CRYPTO")
    max_line = f"\n(حداکثر مبلغ: {max_topup:,} تومان)" if max_topup else ""
    await callback.message.answer(
        f"🪙 لطفاً مبلغی که می‌خواهید با {method_fa} کیف پولتان شارژ شود را به تومان وارد کنید:\n"
        f"(حداقل مبلغ: {min_topup:,} تومان){max_line}",
        reply_markup=await back_to_wallet_keyboard(),
    )
    await state.set_state(WalletStates.waiting_crypto_amount)
    await callback.answer()


@router.message(WalletStates.waiting_crypto_amount)
async def crypto_pay_amount(message: Message, state: FSMContext):
    amount, err = await validate_topup_amount(message.text, "CRYPTO")
    if err:
        await message.answer(err)
        return

    data = await state.get_data()
    method = data.get("crypto_method", "TON")

    from crypto import (
        get_gram_toman_rate, get_usdc_toman_rate, get_crypto_config, generate_comment,
    )

    rate = await get_gram_toman_rate() if method == "TON" else await get_usdc_toman_rate()
    if not rate:
        await message.answer(
            "⚠️ در حال حاضر نرخ لحظه‌ای در دسترس نیست. لطفاً چند لحظه دیگه دوباره امتحان کنید "
            "یا از «پشتیبانی» کمک بگیرید.",
            reply_markup=await wallet_menu_keyboard(),
        )
        await state.clear()
        return

    crypto_cfg = await get_crypto_config()
    address = crypto_cfg.ton_address if method == "TON" else crypto_cfg.bsc_address
    if not address:
        await message.answer(
            "⚠️ آدرس ولت برای این روش هنوز تنظیم نشده. لطفاً از روش دیگه‌ای استفاده کنید یا با پشتیبانی تماس بگیرید.",
            reply_markup=await wallet_menu_keyboard(),
        )
        await state.clear()
        return

    crypto_amount = round(amount / rate, 4)
    comment = None
    if method == "TON" and crypto_cfg.comment_enabled:
        comment = generate_comment()

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CRYPTO_PAYMENT_MINUTES)

    async with async_session() as session:
        tx = WalletTransaction(
            user_id=message.from_user.id, amount=amount, transaction_type="DEPOSIT",
            method=method, status="AWAITING_CRYPTO", expires_at=expires_at,
            crypto_amount=crypto_amount, crypto_comment=comment,
            description=f"در انتظار واریز {crypto_amount} {'TON' if method == 'TON' else 'USDC'}",
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)

    unit = "TON" if method == "TON" else "USDC"
    text_lines = [
        f"🪙 نرخ لحظه‌ای: هر ۱ {unit} ≈ {rate:,.0f} تومان\n",
        f"با واریز <b>{crypto_amount} {unit}</b>، کیف پول شما <b>{amount:,} تومان</b> شارژ می‌شود.\n",
        f"⏳ مهلت شما: {CRYPTO_PAYMENT_MINUTES} دقیقه\n",
        f"آدرس مقصد (روی متن بزنید تا کپی بشه):\n<code>{address}</code>",
    ]
    if comment:
        text_lines.append(
            f"\n{crypto_cfg.comment_prompt or 'کامنت تراکنش'} (حتماً دقیقاً همینو بذارید):\n<code>{comment}</code>"
        )
    text_lines.append(f"\nمقدار دقیق واریزی:\n<code>{crypto_amount}</code>")

    await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=await back_to_wallet_keyboard())

    copy_rows = [
        [InlineKeyboardButton(text="📋 کپی آدرس مقصد", copy_text=CopyTextButton(text=address))],
        [InlineKeyboardButton(text="📋 کپی مقدار واریزی", copy_text=CopyTextButton(text=str(crypto_amount)))],
    ]
    if comment:
        copy_rows.append([InlineKeyboardButton(text="📋 کپی کامنت", copy_text=CopyTextButton(text=comment))])
    copy_rows.append([InlineKeyboardButton(text="🔄 بررسی پرداخت", callback_data=f"cryptocheck_{tx.id}")])
    await message.answer("👆 برای کپی سریع از دکمه‌های زیر استفاده کن:",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=copy_rows))

    await state.clear()

    await _notify_admins(
        message.bot,
        f"🪙 شروع پرداخت ارزی جدید\n\n"
        f"کاربر: {message.from_user.id}\nروش: {unit}\nمبلغ: {amount:,} تومان "
        f"({crypto_amount} {unit})\nشماره تراکنش: #{tx.id}\nوضعیت: در انتظار واریز کاربر",
    )


@router.callback_query(F.data.startswith("cryptocheck_"))
async def crypto_check_cb(callback: CallbackQuery):
    tx_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "AWAITING_CRYPTO":
            await callback.answer("این تراکنش دیگه در انتظار نیست.", show_alert=True)
            return
        crypto_cfg = await get_crypto_config_local(session)

    from crypto import check_ton_payment, check_bsc_usdc_payment
    if tx.method == "TON":
        paid = await check_ton_payment(crypto_cfg.ton_address, tx.crypto_amount, tx.crypto_comment,
                                        crypto_cfg.ton_api_key)
    else:
        paid = await check_bsc_usdc_payment(crypto_cfg.bsc_address, tx.crypto_amount, crypto_cfg.bscscan_api_key)

    if not paid:
        await callback.answer("⏳ هنوز تراکنشی پیدا نشد. چند دقیقه بعد از واریز دوباره امتحان کن.", show_alert=True)
        return

    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if tx.status != "AWAITING_CRYPTO":
            await callback.answer("قبلاً پردازش شده.", show_alert=True)
            return
        user = await session.scalar(select(User).where(User.user_id == tx.user_id))
        user.balance += tx.amount
        tx.status = "SUCCESS"
        await session.commit()

    await callback.message.edit_text(f"✅ پرداخت تایید شد و {tx.amount:,} تومان به کیف پولت اضافه شد.")
    await callback.answer("✅ شارژ شد!")

    unit = "TON" if tx.method == "TON" else "USDC"
    await _notify_admins(
        callback.bot,
        f"✅ پرداخت ارزی تکمیل شد\n\n"
        f"کاربر: {tx.user_id}\nروش: {unit}\nمبلغ: {tx.amount:,} تومان\nشماره تراکنش: #{tx.id}",
    )


async def get_crypto_config_local(session):
    from database import CryptoConfig
    cfg_row = await session.scalar(select(CryptoConfig).limit(1))
    return cfg_row


# ==================== درگاه پرداخت (HooshPay) ====================

@router.message(ButtonText("btn_wallet_gateway"))
async def gateway_pay(message: Message, state: FSMContext):
    if not await is_payment_method_enabled("GATEWAY"):
        await message.answer("⚠️ روش پرداخت از درگاه فعلاً غیرفعاله. لطفاً از روش دیگه‌ای استفاده کن.",
                              reply_markup=await wallet_menu_keyboard())
        return
    if not await _check_open_or_continue(message, state):
        return
    min_topup = await get_min_topup("GATEWAY")
    max_topup = await get_max_topup("GATEWAY")
    max_line = f"\n(حداکثر مبلغ: {max_topup:,} تومان)" if max_topup else ""
    await message.answer(
        f"🌐 لطفاً مبلغی که می‌خواهید واریز کنید را به تومان وارد کنید:\n"
        f"(حداقل مبلغ: {min_topup:,} تومان){max_line}",
        reply_markup=await back_to_wallet_keyboard(),
    )
    await state.set_state(WalletStates.waiting_gateway_amount)


@router.message(WalletStates.waiting_gateway_amount)
async def gateway_amount_in(message: Message, state: FSMContext):
    amount, err = await validate_topup_amount(message.text, "GATEWAY")
    if err:
        await message.answer(err)
        return

    user_id = message.from_user.id

    from hooshpay import create_invoice, make_order_id
    invoice, error = await create_invoice(amount, make_order_id(user_id), "شارژ کیف پول Lidso")

    if not invoice:
        await message.answer(
            "❌ در حال حاضر درگاه پرداخت در دسترس نیست. لطفاً از «کارت به کارت» یا «پرداخت ارزی» استفاده کنید.",
            reply_markup=await wallet_menu_keyboard(),
        )
        await _notify_admins(message.bot, f"⚠️ ساخت فاکتور HooshPay fail شد (کاربر {user_id}):\n{error}")
        await state.clear()
        return

    async with async_session() as session:
        tx = WalletTransaction(
            user_id=user_id, amount=amount, transaction_type="DEPOSIT",
            method="GATEWAY", status="AWAITING_GATEWAY",
            gateway_invoice_uid=invoice["uid"],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=GATEWAY_PAYMENT_MINUTES),
            description=f"فاکتور HooshPay #{invoice['uid']}",
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 پرداخت", url=invoice["payment_url"])],
        [InlineKeyboardButton(text="🔄 بررسی پرداخت", callback_data=f"gwcheck_{tx.id}")],
    ])
    await message.answer(
        f"🌐 مبلغ قابل پرداخت: {invoice['payable_amount']:,} تومان\n"
        f"(ممکنه چند تومان با مبلغ درخواستی فرق داشته باشه تا پرداختت دقیق تشخیص داده بشه)\n\n"
        f"روی «پرداخت» بزن، بعد از پرداخت خودکار یا با زدن «بررسی پرداخت» کیف پولت شارژ میشه.",
        reply_markup=kb,
    )
    await state.clear()

    await _notify_admins(
        message.bot,
        f"🌐 شروع پرداخت از درگاه جدید\n\n"
        f"کاربر: {user_id}\nروش: درگاه پرداخت (HooshPay)\nمبلغ: {amount:,} تومان\n"
        f"شماره تراکنش: #{tx.id}\nوضعیت: در انتظار پرداخت کاربر",
    )


@router.callback_query(F.data.startswith("gwcheck_"))
async def gateway_check_cb(callback: CallbackQuery):
    tx_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "AWAITING_GATEWAY":
            await callback.answer("این تراکنش دیگه در انتظار نیست.", show_alert=True)
            return

    from hooshpay import verify_invoice
    paid, data, error = await verify_invoice(tx.gateway_invoice_uid)

    if error:
        await callback.answer("خطا در بررسی وضعیت. چند لحظه دیگه دوباره امتحان کن.", show_alert=True)
        return

    if not paid:
        await callback.answer("⏳ هنوز پرداخت ثبت نشده. اگه پرداخت کردی، چند ثانیه صبر کن و دوباره بزن.",
                               show_alert=True)
        return

    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if tx.status != "AWAITING_GATEWAY":
            await callback.answer("قبلاً پردازش شده.", show_alert=True)
            return
        user = await session.scalar(select(User).where(User.user_id == tx.user_id))
        user.balance += tx.amount
        tx.status = "SUCCESS"
        await session.commit()

    await callback.message.edit_text(f"✅ پرداخت تایید شد و {tx.amount:,} تومان به کیف پولت اضافه شد.")
    await callback.answer("✅ شارژ شد!")

    await _notify_admins(
        callback.bot,
        f"✅ پرداخت از درگاه تکمیل شد\n\n"
        f"کاربر: {tx.user_id}\nروش: درگاه پرداخت (HooshPay)\nمبلغ: {tx.amount:,} تومان\n"
        f"شماره تراکنش: #{tx.id}",
    )


# ==================== کد تخفیف ====================

@router.message(ButtonText("btn_wallet_discount"))
async def promo_start(message: Message, state: FSMContext):
    await message.answer("🎁 لطفاً کد تخفیف خود را وارد کنید:", reply_markup=await back_to_wallet_keyboard())
    await state.set_state(WalletStates.waiting_for_promo)


@router.message(WalletStates.waiting_for_promo)
async def promo_process(message: Message, state: FSMContext):
    code_in = message.text.strip().upper()
    user_id = message.from_user.id

    async with async_session() as session:
        promo = await session.scalar(
            select(DiscountCode).where(DiscountCode.code == code_in, DiscountCode.active == True)
        )
        if not promo or promo.current_uses >= promo.max_uses:
            await message.answer("❌ کد تخفیف نامعتبر یا منقضی شده است.", reply_markup=await wallet_menu_keyboard())
            await state.clear()
            return

        used = await session.scalar(
            select(UsedDiscount).where(UsedDiscount.user_id == user_id, UsedDiscount.code == code_in)
        )
        if used:
            await message.answer("⚠️ شما قبلاً از این کد تخفیف استفاده کرده‌اید!", reply_markup=await wallet_menu_keyboard())
            await state.clear()
            return

        # نکته: اینجا current_uses افزایش داده نمیشه و UsedDiscount ثبت نمیشه - چون کاربر هنوز
        # واقعاً از تخفیف استفاده نکرده (فقط رزرو کرده). مصرف واقعی کد فقط موقع خرید واقعی
        # (توی shop.py) ثبت میشه - وگرنه اگه کاربر منصرف بشه یا موجودی کافی نداشته باشه، کد
        # برای همیشه سوخته میشد بدون اینکه واقعاً استفاده بشه.
        user = await session.scalar(select(User).where(User.user_id == user_id))
        user.pending_discount_code = code_in
        user.pending_discount_percent = promo.percent
        await session.commit()

        await message.answer(
            f"🎉 کد تخفیف {promo.percent}% با موفقیت ثبت شد!\n"
            f"این تخفیف در اولین خرید شما به صورت خودکار اعمال می‌شود.",
            reply_markup=await main_keyboard(),
        )

    await state.clear()
