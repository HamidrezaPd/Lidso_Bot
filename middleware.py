"""
میدلور عضویت اجباری کانال.
اگه از /admin یه آیدی کانال (مثلا @lidsochannel) تنظیم شده باشه، کاربرهای عادی (غیر از ادمین‌ها)
قبل از استفاده از هر بخش ربات باید عضو اون کانال باشن؛ در غیر این صورت فقط یه پیام با دکمه‌ی
«عضویت» و «عضو شدم» می‌بینن.

نکته مهم: بات باید از قبل توی اون کانال «ادمین» باشه، وگرنه get_chat_member کار نمی‌کنه.
"""
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from database import async_session, BotContent
import config as cfg


async def get_required_channel():
    async with async_session() as session:
        val = await session.scalar(select(BotContent.value).where(BotContent.key == "required_channel"))
    return (val or "").strip() or None


def channel_link(channel: str) -> str:
    if channel.startswith("http"):
        return channel
    return f"https://t.me/{channel.lstrip('@')}"


async def is_member(bot, channel: str, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        # اگه بات هنوز ادمین کانال نشده یا خطای دیگه‌ای پیش اومد، کاربر رو قفل نمی‌کنیم
        # (بهتره ربات کار کنه تا اینکه به‌خاطر تنظیم اشتباه، کلاً همه رو بلاک کنه)
        return True


def join_prompt_kb(channel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 عضویت در کانال", url=channel_link(channel))],
        [InlineKeyboardButton(text="✅ عضو شدم", callback_data="check_membership")],
    ])


class ChannelMembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        # ادمین‌ها همیشه معاف‌ان
        if user.id in cfg.ADMIN_IDS:
            return await handler(event, data)

        # دکمه‌ی «عضو شدم» همیشه باید اجرا بشه تا خودش وضعیت رو دوباره چک کنه
        if isinstance(event, CallbackQuery) and event.data == "check_membership":
            return await handler(event, data)

        channel = await get_required_channel()
        if not channel:
            return await handler(event, data)

        if await is_member(event.bot, channel, user.id):
            return await handler(event, data)

        text = "⚠️ برای استفاده از ربات، ابتدا باید عضو کانال ما بشید:"
        kb = join_prompt_kb(channel)
        if isinstance(event, Message):
            await event.answer(text, reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.answer()
            try:
                await event.message.answer(text, reply_markup=kb)
            except Exception:
                pass
        return  # اجازه نمیده به هندلر اصلی برسه

# ==================== Rate Limit / Cooldown امنیتی ====================
# هر کاربر عادی حداکثر یک درخواست پردازش‌شده در هر ۳ ثانیه.
# این محدودیت در حافظه نگه‌داری می‌شود و با restart ربات reset می‌شود.
# ادمین‌ها مستثنا هستند تا پنل مدیریت و عملیات اضطراری دچار تأخیر نشود.

import asyncio
import time
from collections import defaultdict


class UserCooldownMiddleware(BaseMiddleware):
    """Simple per-user cooldown to prevent request flooding without touching the DB."""

    COOLDOWN_SECONDS = 3.0

    def __init__(self, cooldown_seconds: float = COOLDOWN_SECONDS):
        super().__init__()
        self.cooldown_seconds = float(cooldown_seconds)
        self._last_processed: dict[int, float] = {}
        self._last_warning: dict[int, float] = {}
        self._locks = defaultdict(asyncio.Lock)

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        # ادمین‌ها برای جلوگیری از اختلال در پنل مدیریت و عملیات اضطراری محدود نمی‌شوند.
        if user.id in cfg.ADMIN_IDS:
            return await handler(event, data)

        now = time.monotonic()

        async with self._locks[user.id]:
            last = self._last_processed.get(user.id)
            if last is not None:
                remaining = self.cooldown_seconds - (now - last)
                if remaining > 0:
                    # جلوی ارسال چندین پیام هشدار پشت سر هم را هم می‌گیریم.
                    last_warning = self._last_warning.get(user.id, 0.0)
                    if now - last_warning >= 1.0:
                        self._last_warning[user.id] = now
                        seconds = max(1, int(remaining + 0.999))
                        text = f"⏳ لطفاً {seconds} ثانیه صبر کنید و بعد درخواست بعدی را ارسال کنید."

                        if isinstance(event, CallbackQuery):
                            try:
                                await event.answer(text, show_alert=False)
                            except Exception:
                                pass
                        elif isinstance(event, Message):
                            try:
                                await event.answer(text)
                            except Exception:
                                pass
                    return

            # زمان را بعد از اجرای موفق هندلر ثبت می‌کنیم تا پیام‌های نامرتبط/ردشده
            # بی‌دلیل cooldown ایجاد نکنند.
            result = await handler(event, data)
            self._last_processed[user.id] = time.monotonic()

            # پاک‌سازی سبک رکوردهای قدیمی؛ نیازی به دیتابیس یا migration ندارد.
            if len(self._last_processed) > 10000:
                cutoff = time.monotonic() - max(self.cooldown_seconds * 4, 60.0)
                for uid, ts in list(self._last_processed.items()):
                    if ts < cutoff:
                        self._last_processed.pop(uid, None)
                        self._last_warning.pop(uid, None)
                        self._locks.pop(uid, None)

            return result
