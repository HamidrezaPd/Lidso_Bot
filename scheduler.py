"""
جاب پس‌زمینه: هر چند دقیقه یکبار چک می‌کنه
1) سرویس‌هایی که تازه منقضی شدن -> به کاربر پیام یادآوری تمدید می‌فرسته
2) سرویس‌هایی که بعد از مهلت (مثلا 72 ساعت) هنوز تمدید نشدن -> از پنل حذف و در دیتابیس REMOVED میشن
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select

from database import async_session
from operation_locks import user_operation_lock
from database import ServiceOrder, Panel
from panels import delete_panel_account, is_panel_account_exhausted
import config as cfg

logger = logging.getLogger(__name__)


def _as_aware(dt):
    """SQLite تاریخ‌ها رو بدون timezone (naive) برمی‌گردونه، ولی همه‌جای کد از aware (UTC)
    استفاده می‌کنیم. این تابع قبل از هر مقایسه‌ی دستی (نه توی SQL query) لازمه، وگرنه
    Python با خطای 'can't compare offset-naive and offset-aware datetimes' کرش می‌کنه."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def check_expiring_services(bot: Bot):
    """این تابع فقط سرویس‌های پولی (غیر-تست) رو مدیریت می‌کنه. سرویس‌های تست رایگان منطق
    جدا و فوری‌تری دارن (بدون مهلت گریس) - نگاه کن به check_expired_trials پایین‌تر."""
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # ---- یادآوری برای سرویس‌های تازه منقضی‌شده ----
        expiring = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.status == "ACTIVE",
                ServiceOrder.is_trial == False,
                ServiceOrder.renew_notified == False,
                ServiceOrder.expire_at <= now,
            )
        )).scalars().all()

        for order in expiring:
            try:
                await bot.send_message(
                    order.user_id,
                    f"⚠️ سرویس «{order.config_name}» شما به پایان رسیده (حجم یا مدت زمان تمام شده).\n"
                    f"برای جلوگیری از حذف شدن سرویس، حداکثر تا {cfg.RENEWAL_GRACE_HOURS} ساعت آینده "
                    f"از منوی «تمدید اشتراک» اقدام کنید.",
                )
            except Exception:
                pass
            order.renew_notified = True
            order.renew_notified_at = now

        await session.commit()

        # ---- حذف خودکار بعد از اتمام مهلت ----
        cutoff = now - timedelta(hours=cfg.RENEWAL_GRACE_HOURS)
        to_remove = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.status == "ACTIVE",
                ServiceOrder.is_trial == False,
                ServiceOrder.renew_notified == True,
                ServiceOrder.renew_notified_at <= cutoff,
            )
        )).scalars().all()

        for order in to_remove:
            if order.panel_id:
                try:
                    panel = await session.get(Panel, order.panel_id)
                    if panel:
                        await delete_panel_account(panel, order.config_name)
                except Exception as e:
                    logger.warning(f"خطا در حذف کانفیگ {order.config_name} از پنل: {e}")

            if order.phantom_token:
                try:
                    from submerge import delete_from_phantom
                    phantom_err = await delete_from_phantom(order.phantom_token)
                    if phantom_err:
                        logger.warning(f"⚠️ حذف توکن {order.phantom_token} از PhantomHubs fail شد: {phantom_err}")
                except Exception as e:
                    logger.warning(f"خطا در حذف توکن {order.phantom_token} از PhantomHubs: {e}")

            order.status = "REMOVED"
            try:
                await bot.send_message(
                    order.user_id,
                    f"🗑 سرویس «{order.config_name}» به دلیل عدم تمدید در مهلت مقرر حذف شد.",
                )
            except Exception:
                pass

        await session.commit()


TRIAL_CHECK_SECONDS = 30  # هر ۳۰ ثانیه یک‌بار چک تست‌ها (هم زمان، هم مصرف زنده روی پنل)


async def check_expired_trials(bot: Bot):
    """
    سرویس‌های تست رایگان، برخلاف سرویس‌های پولی، هیچ مهلت گریسی ندارن - به محض اینکه مدت
    زمانشون تموم بشه یا حجمشون (روی خودِ پنل) تموم بشه، فوراً از پنل و از PhantomHubs (اگه
    ادغام ساب داشتن) حذف میشن.
    """
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        active_trials = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.status == "ACTIVE",
                ServiceOrder.is_trial == True,
            )
        )).scalars().all()

        to_remove = []
        order_reasons = {}
        for order in active_trials:
            try:
                expired_by_time = order.expire_at is not None and _as_aware(order.expire_at) <= now
                expired_by_volume = False

                if not expired_by_time and order.panel_id:
                    try:
                        panel = await session.get(Panel, order.panel_id)
                        if panel:
                            expired_by_volume = await is_panel_account_exhausted(panel, order.config_name)
                    except Exception as e:
                        logger.warning(f"خطا در چک مصرف تست {order.config_name}: {e}")

                if expired_by_time or expired_by_volume:
                    logger.info(
                        f"🗑 تست رایگان {order.config_name} (کاربر {order.user_id}) منقضی شد "
                        f"(زمان: {expired_by_time}, حجم: {expired_by_volume}) - در حال حذف..."
                    )
                    order_reasons[order.id] = "زمان" if expired_by_time else "حجم"
                    to_remove.append(order)
            except Exception as e:
                # هر خطای پیش‌بینی‌نشده‌ای (نه فقط شبکه) روی یه سفارش، نباید کل batch رو متوقف کنه
                logger.exception(f"خطای غیرمنتظره موقع چک تست {order.config_name}: {e}")

        for order in to_remove:
            reason = order_reasons.get(order.id, "زمان یا حجم")

            if order.panel_id:
                try:
                    panel = await session.get(Panel, order.panel_id)
                    if panel:
                        await delete_panel_account(panel, order.config_name)
                except Exception as e:
                    logger.warning(f"خطا در حذف کانفیگ تست {order.config_name} از پنل: {e}")

            order.status = "REMOVED"
            if order.phantom_token:
                try:
                    from submerge import delete_from_phantom
                    phantom_err = await delete_from_phantom(order.phantom_token)
                    if phantom_err:
                        logger.warning(f"⚠️ حذف توکن تست {order.phantom_token} از PhantomHubs fail شد: {phantom_err}")
                    else:
                        logger.info(f"✅ توکن تست {order.phantom_token} از PhantomHubs حذف شد.")
                except Exception as e:
                    logger.warning(f"خطا در حذف توکن تست {order.phantom_token} از PhantomHubs: {e}")
            else:
                logger.info(f"ℹ️ تست {order.config_name} توکن PhantomHubs نداشت (ادغام ساب براش فعال نبوده) - رد شد.")

            try:
                await bot.send_message(
                    order.user_id,
                    f"⏳ سرویس تست رایگان «{order.config_name}» شما به پایان رسید ({reason} تمام شد) و حذف شد.\n"
                    f"امیدواریم از کیفیت سرویس راضی بوده باشید! برای ادامه، از منوی «خرید سرویس» "
                    f"می‌تونید یکی از پلن‌های اصلی رو تهیه کنید.",
                )
            except Exception:
                pass

        if to_remove:
            await session.commit()


async def trial_expiry_loop(bot: Bot):
    logger.info("✅ حلقه‌ی چک خودکار سرویس‌های تست رایگان شروع به کار کرد.")
    while True:
        try:
            await check_expired_trials(bot)
        except Exception as e:
            logger.exception(f"خطا در چک کردن سرویس‌های تست منقضی‌شده: {e}")
        await asyncio.sleep(TRIAL_CHECK_SECONDS)


async def scheduler_loop(bot: Bot):
    while True:
        try:
            await check_expiring_services(bot)
        except Exception as e:
            logger.exception(f"خطا در اجرای زمان‌بند: {e}")
        await asyncio.sleep(cfg.SCHEDULER_INTERVAL_MINUTES * 60)


GATEWAY_POLL_SECONDS = 15


async def check_gateway_payments(bot: Bot):
    """هر چند ثانیه چک می‌کنه ببینه فاکتورهای درگاه پرداخت (HooshPay) که در انتظارن، پرداخت شدن یا نه"""
    from database import WalletTransaction, User
    from hooshpay import verify_invoice

    async with async_session() as session:
        pending_tx = (await session.execute(
            select(WalletTransaction).where(WalletTransaction.status == "AWAITING_GATEWAY")
        )).scalars().all()

    for tx in pending_tx:
        try:
            paid, data, error = await verify_invoice(tx.gateway_invoice_uid)
        except Exception as e:
            logger.warning(f"خطا در بررسی فاکتور {tx.gateway_invoice_uid}: {e}")
            continue

        if error or not paid:
            continue

        async with user_operation_lock(tx.user_id):
            async with async_session() as session:
                fresh_tx = await session.get(WalletTransaction, tx.id)
                if not fresh_tx or fresh_tx.status != "AWAITING_GATEWAY":
                    continue  # یکی دیگه (مثلاً چک دستی کاربر) قبلاً پردازشش کرده
                user = await session.scalar(select(User).where(User.user_id == fresh_tx.user_id))
                user.balance += fresh_tx.amount
                fresh_tx.status = "SUCCESS"
                await session.commit()

        try:
            await bot.send_message(
                tx.user_id,
                f"✅ پرداخت شما تایید شد و {tx.amount:,} تومان به کیف پولتان اضافه شد.",
            )
        except Exception:
            pass


async def gateway_poll_loop(bot: Bot):
    while True:
        try:
            await check_gateway_payments(bot)
        except Exception as e:
            logger.exception(f"خطا در چک کردن پرداخت‌های درگاه: {e}")
        await asyncio.sleep(GATEWAY_POLL_SECONDS)


CRYPTO_POLL_SECONDS = 20


async def check_crypto_payments(bot: Bot):
    """هر چند ثانیه چک می‌کنه ببینه پرداخت‌های ارزی (گرام/USDC) در انتظار، رسیده یا نه"""
    from database import WalletTransaction, User, CryptoConfig
    from crypto import check_ton_payment, check_bsc_usdc_payment

    async with async_session() as session:
        crypto_cfg = await session.scalar(select(CryptoConfig).limit(1))
        if not crypto_cfg:
            return
        pending_tx = (await session.execute(
            select(WalletTransaction).where(WalletTransaction.status == "AWAITING_CRYPTO")
        )).scalars().all()

    for tx in pending_tx:
        try:
            if tx.method == "TON":
                paid = await check_ton_payment(crypto_cfg.ton_address, tx.crypto_amount, tx.crypto_comment,
                                                crypto_cfg.ton_api_key)
            else:
                paid = await check_bsc_usdc_payment(crypto_cfg.bsc_address, tx.crypto_amount,
                                                     crypto_cfg.bscscan_api_key)
        except Exception as e:
            logger.warning(f"خطا در چک تراکنش ارزی #{tx.id}: {e}")
            continue

        if not paid:
            continue

        async with user_operation_lock(tx.user_id):
            async with async_session() as session:
                fresh_tx = await session.get(WalletTransaction, tx.id)
                if not fresh_tx or fresh_tx.status != "AWAITING_CRYPTO":
                    continue
                user = await session.scalar(select(User).where(User.user_id == fresh_tx.user_id))
                user.balance += fresh_tx.amount
                fresh_tx.status = "SUCCESS"
                await session.commit()

        try:
            await bot.send_message(
                tx.user_id,
                f"✅ واریز ارزی شما تایید شد و {tx.amount:,} تومان به کیف پولتان اضافه شد.",
            )
        except Exception:
            pass


async def crypto_poll_loop(bot: Bot):
    while True:
        try:
            await check_crypto_payments(bot)
        except Exception as e:
            logger.exception(f"خطا در چک کردن پرداخت‌های ارزی: {e}")
        await asyncio.sleep(CRYPTO_POLL_SECONDS)


PAYMENT_EXPIRE_CHECK_SECONDS = 20


async def check_expired_payments(bot: Bot):
    """
    هر چند ثانیه چک می‌کنه: تراکنش‌هایی که هنوز در حالت انتظار بودن (کارت به کارت منتظر رسید،
    یا درگاه/کریپتو منتظر تکمیل پرداخت) و مهلت‌شون (مثلاً ۲۰ دقیقه) تموم شده رو خودکار EXPIRED
    می‌کنه و فوراً به کاربر پیام میده - بدون اینکه منتظر اقدام بعدی کاربر بمونه.
    توجه: PENDING (رسید کارت به کارت که ارسال شده) اینجا expire نمیشه - چون واقعاً منتظر
    بررسی دستی ادمینه و کاربر کاری از دستش برنمیاد که با گذشت زمان بی‌اعتبار بشه.
    """
    from database import WalletTransaction
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        expired = (await session.execute(
            select(WalletTransaction).where(
                WalletTransaction.status.in_(["AWAITING_RECEIPT", "AWAITING_GATEWAY", "AWAITING_CRYPTO"]),
                WalletTransaction.expires_at.isnot(None),
                WalletTransaction.expires_at <= now,
                WalletTransaction.expire_notified == False,
            )
        )).scalars().all()

        for tx in expired:
            tx.status = "EXPIRED"
            tx.expire_notified = True

        await session.commit()

    method_fa_map = {
        "CARD": "کارت به کارت", "GATEWAY": "درگاه پرداخت",
        "TON": "پرداخت ارزی (TON)", "USDC": "پرداخت ارزی (USDC)",
    }
    for tx in expired:
        try:
            method_fa = method_fa_map.get(tx.method, tx.method)
            await bot.send_message(
                tx.user_id,
                f"⏳ پرداخت شما ({method_fa} - {tx.amount:,} تومان) به دلیل پایان زمان مهلت پرداخت لغو شد.\n"
                f"در صورت تمایل می‌تونید از منوی «کیف پول» یه پرداخت جدید شروع کنید.",
            )
        except Exception:
            pass


async def expired_payments_loop(bot: Bot):
    while True:
        try:
            await check_expired_payments(bot)
        except Exception as e:
            logger.exception(f"خطا در چک کردن پرداخت‌های منقضی: {e}")
        await asyncio.sleep(PAYMENT_EXPIRE_CHECK_SECONDS)
