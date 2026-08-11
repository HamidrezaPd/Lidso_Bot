import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from config import BOT_TOKEN, PROXY_URL
from database import init_db
from seed_data import seed_defaults
from ui_texts import seed_ui_texts
from scheduler import scheduler_loop, gateway_poll_loop, crypto_poll_loop, expired_payments_loop, trial_expiry_loop

from handlers.user import router as user_router
from handlers.shop import router as shop_router
from handlers.wallet import router as wallet_router
from handlers.admin import router as admin_router
from handlers.trial import router as trial_router
from middleware import ChannelMembershipMiddleware, UserCooldownMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN تنظیم نشده. فایل .env رو بساز و توکن ربات رو توش بذار.")

    await init_db()
    await seed_defaults()
    await seed_ui_texts()

    # 🟢 اگه توی ایران هستی و تلگرام فیلتره، تو فایل .env مقدار PROXY_URL رو ست کن
    # مثلا PROXY_URL=socks5://127.0.0.1:10808 (پورت پروکسی لوکال خودت)
    logger.info(f"مقدار PROXY_URL خونده‌شده از .env: {PROXY_URL!r}")

    if PROXY_URL:
        session = AiohttpSession(proxy=PROXY_URL)
        bot = Bot(token=BOT_TOKEN, session=session)
        logger.info(f"در حال اتصال از طریق پروکسی: {PROXY_URL}")
    else:
        logger.info("هیچ پروکسی‌ای تنظیم نشده - داره مستقیم به تلگرام وصل میشه.")
        bot = Bot(token=BOT_TOKEN)

    # 🔵 دکمه‌ی همیشگی «Menu» کنار جعبه‌ی تایپ تلگرام - حتی اگه کاربر چت رو پاک کنه،
    # همیشه از همینجا می‌تونه /start رو بزنه و ربات دوباره راه بیفته
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 شروع / منوی اصلی"),
        ])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logger.warning(f"تنظیم دکمه منوی تلگرام fail شد (مشکلی نیست، ربات عادی کار می‌کنه): {e}")

    dp = Dispatcher(storage=MemoryStorage())

    # عضویت اجباری کانال (اگه از /admin تنظیم شده باشه) - قبل از هر هندلری چک میشه
    dp.message.middleware(ChannelMembershipMiddleware())
    dp.callback_query.middleware(ChannelMembershipMiddleware())

    # Rate limit امنیتی: هر کاربر عادی حداکثر یک درخواست پردازش‌شده در هر ۳ ثانیه.
    # ادمین‌ها مستثنا هستند. این middleware قبل از handlerها اجرا می‌شود و دیتابیس را تغییر نمی‌دهد.
    cooldown_middleware = UserCooldownMiddleware()
    dp.message.middleware(cooldown_middleware)
    dp.callback_query.middleware(cooldown_middleware)

    # user_router اول چک میشه تا /start و ناوبری اصلی همیشه قطعی کار کنن، حتی اگه یه جای دیگه
    # (مثلاً یه متن دکمه‌ی دیگه) به‌اشتباه با یه فرمان قاطی بشه
    dp.include_router(user_router)
    dp.include_router(shop_router)
    dp.include_router(wallet_router)
    dp.include_router(trial_router)
    dp.include_router(admin_router)

    asyncio.create_task(scheduler_loop(bot))
    asyncio.create_task(gateway_poll_loop(bot))
    asyncio.create_task(crypto_poll_loop(bot))
    asyncio.create_task(expired_payments_loop(bot))
    asyncio.create_task(trial_expiry_loop(bot))

    logger.info("🤖 ربات Lidso با موفقیت روشن شد.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
