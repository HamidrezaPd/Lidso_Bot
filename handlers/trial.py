"""
هندلر تست رایگان - دکمه‌ی «🎁 تست رایگان» توی منوی اصلی

منطق:
- هر کاربر (بر اساس user_id ثابت توی دیتابیس) فقط یه‌بار می‌تونه از تست رایگان استفاده کنه،
  مگر اینکه ادمین از بات ادمین این محدودیت رو براش (یا برای همه) ریست کنه.
- اگه چند TrialPlan فعال باشه، کاربر یه لیست می‌بینه و یکی رو انتخاب می‌کنه.
- اسم کانفیگ طبق فرمت {prefix}_{حجم به مگابایت}mb_{عدد رندوم} ساخته میشه، مثلا LidsoTest_100mb_483.
- ادغام ساب فقط اگه هم global هم فلگ اختصاصی پنل فعال باشه اعمال میشه (دقیقاً مثل خرید عادی).
"""
import logging
import random

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from database import async_session, User, TrialPlan, Panel, ServiceOrder, BotContent
from ui_texts import ButtonText
from keyboards.user_kb import main_keyboard
from panels import create_panel_account
from submerge import apply_sub_merge
import config as cfg
from operation_locks import user_operation_lock

router = Router()
logger = logging.getLogger(__name__)


async def _notify_admins(bot, text):
    for admin_id in cfg.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def get_content(session, key, default="", **variables):
    from ui_texts import get_managed_text
    return await get_managed_text(key, default, **variables)


def _trial_config_name(trial: TrialPlan) -> str:
    """مثلا LidsoTest_100mb_483 - عدد آخر رندومه (نه ترتیبی)، چون تست‌ها معمولاً پرتکرارن
    و رندوم بودن احتمال تصادم رو عملاً صفر می‌کنه بدون نیاز به کوئری گرفتن از پنل هر بار."""
    mb = int(round((trial.volume_gb or 0) * 1024))
    rand = random.randint(100, 999999)
    prefix = trial.prefix or "LidsoTest"
    return f"{prefix}_{mb}mb_{rand}"


@router.message(ButtonText("btn_free_trial"))
async def free_trial_start(message: Message):
    user_id = message.from_user.id

    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == user_id))
        if user and user.used_free_trial:
            await message.answer(
                "⚠️ شما قبلاً از تست رایگان استفاده کرده‌اید.\n"
                "هر کاربر فقط یک‌بار می‌تونه از این امکان استفاده کنه.",
                reply_markup=await main_keyboard(),
            )
            return

        trials = (await session.execute(
            select(TrialPlan).where(TrialPlan.active == True).order_by(TrialPlan.sort_order)
        )).scalars().all()

    if not trials:
        await message.answer(
            "⚠️ در حال حاضر تست رایگانی در دسترس نیست.",
            reply_markup=await main_keyboard(),
        )
        return

    if len(trials) == 1:
        await _deliver_trial(message, trials[0])
        return

    rows = [[InlineKeyboardButton(text=f"🎁 {t.name}", callback_data=f"trialpick_{t.id}")] for t in trials]
    await message.answer("🎁 کدوم تست رایگان رو می‌خوای امتحان کنی؟",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("trialpick_"))
async def free_trial_pick(callback: CallbackQuery):
    trial_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == callback.from_user.id))
        if user and user.used_free_trial:
            await callback.answer("⚠️ شما قبلاً از تست رایگان استفاده کرده‌اید.", show_alert=True)
            return
        trial = await session.get(TrialPlan, trial_id)
        if not trial or not trial.active:
            await callback.answer("این تست رایگان دیگه در دسترس نیست.", show_alert=True)
            return

    await callback.answer()
    await _deliver_trial(callback.message, trial, user_id_override=callback.from_user.id)


async def _deliver_trial(message: Message, trial: TrialPlan, user_id_override: int = None):
    user_id = user_id_override or message.from_user.id

    # جلوگیری از race condition:
    # اگر کاربر هم‌زمان چند بار روی تست رایگان کلیک کند،
    # فقط یکی از درخواست‌ها اجازه‌ی عبور از چک used_free_trial و ساخت سرویس را دارد.
    async with user_operation_lock(user_id, "free_trial"):
        async with async_session() as session:
            # چک نهایی دوباره (جلوگیری از race condition اگه کاربر دوبار سریع کلیک کنه)
            user = await session.scalar(select(User).where(User.user_id == user_id))
            if not user:
                await message.answer("⚠️ لطفاً ابتدا با /start ربات را شروع کنید.")
                return
            if user.used_free_trial:
                await message.answer("⚠️ شما قبلاً از تست رایگان استفاده کرده‌اید.", reply_markup=await main_keyboard())
                return

            panel = await session.get(Panel, trial.panel_id) if trial.panel_id else None
            if not panel:
                await message.answer(
                    "⚠️ این تست رایگان فعلاً به هیچ پنلی وصل نیست. لطفاً بعداً دوباره امتحان کنید یا با پشتیبانی تماس بگیرید.",
                    reply_markup=await main_keyboard(),
                )
                await _notify_admins(message.bot, f"⚠️ تست رایگان «{trial.name}» به هیچ پنلی وصل نیست.")
                return

            processing_text = await get_content(session, "trial_processing_text",
                                                 "⏳ در حال ساخت سرویس تست رایگان شما، چند لحظه صبر کنید...")
            await message.answer(processing_text, reply_markup=await main_keyboard())

            config_name = _trial_config_name(trial)

            try:
                config_link = await create_panel_account(panel, config_name, trial)
                if not config_link or not str(config_link).startswith(
                        ("http://", "https://", "vless://", "vmess://", "trojan://", "ss://")):
                    raise ValueError(f"پاسخ پنل معتبر به نظر نمی‌رسه: {config_link!r}")
            except Exception as e:
                logger.warning(f"خطا در ساخت تست رایگان برای کاربر {user_id}: {e}")
                await message.answer(
                    "❌ متاسفانه در حال حاضر امکان ساخت تست رایگان وجود نداره. لطفاً بعداً دوباره امتحان کنید.",
                    reply_markup=await main_keyboard(),
                )
                await _notify_admins(message.bot, f"⚠️ ساخت تست رایگان برای کاربر {user_id} fail شد:\n{e}")
                return

            phantom_token = None
            try:
                config_link, submerge_error = await apply_sub_merge(config_link, trial, config_name, user_id, panel=panel)
                phantom_token = config_link.split("/token/")[-1] if "/token/" in config_link else None
                logger.info(f"🔗 نتیجه‌ی ادغام ساب برای {config_name}: phantom_token={phantom_token!r}")
                if submerge_error:
                    await _notify_admins(
                        message.bot,
                        f"⚠️ ادغام ساب برای تست رایگان کاربر {user_id} fail شد (لینک خام تحویل داده شد):\n\n{submerge_error}",
                    )
            except Exception as e:
                logger.warning(f"خطا در ادغام ساب تست رایگان کاربر {user_id}: {e}")

            from datetime import datetime, timedelta, timezone
            order = ServiceOrder(
                user_id=user_id, plan_id=trial.id, service_name=trial.name,
                config_name=config_name, config_link=config_link,
                price=0, panel_id=panel.id, status="ACTIVE", is_trial=True,
                phantom_token=phantom_token,
                expire_at=datetime.now(timezone.utc) + timedelta(days=trial.duration_days or 1),
            )
            session.add(order)
            user.used_free_trial = True
            await session.commit()

    caption = (
        f"🎉 تست رایگان شما با موفقیت فعال شد!\n\n"
        f"📦 سرویس: {trial.name}\n"
        f"🔑 نام کانفیگ: `{config_name}`\n\n"
        f"🔗 لینک اتصال:\n`{config_link}`\n\n"
        f"📱 می‌تونی از روی QR کد بالا هم مستقیم اسکن و وصل بشی."
    )
    try:
        from qr_utils import generate_qr_photo
        await message.answer_photo(
            generate_qr_photo(config_link, filename=f"{config_name}.png"),
            caption=caption,
            parse_mode="Markdown",
            reply_markup=await main_keyboard(),
        )
    except Exception as e:
        logger.warning(f"ساخت QR کد برای تست {config_name} fail شد: {e}")
        await message.answer(caption, parse_mode="Markdown", reply_markup=await main_keyboard())

    await _notify_admins(
        message.bot,
        f"🎁 تست رایگان جدید فعال شد\n\nکاربر: {user_id}\nسرویس: {trial.name}\nنام کانفیگ: {config_name}",
    )
