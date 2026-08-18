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
