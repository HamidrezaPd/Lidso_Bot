"""
رجیستری مرکزی متن‌های ثابت ربات (دکمه‌ها و پیام‌های کوتاه).

هدف: هر متنی که توی این فایل تعریف بشه، هم از دیتابیس خونده میشه (قابل ویرایش از /admin)
و هم برای تشخیص کلیک دکمه‌ها یک فیلتر داینامیک (ButtonText) وجود داره که همیشه مقدار
فعلیِ دیتابیس رو چک می‌کنه - پس اگه متن دکمه رو عوض کنی، خود دکمه هم همچنان کار می‌کنه.
"""
import json
from sqlalchemy import select
from aiogram.types import MessageEntity
from database import async_session, BotContent

# ==================== مقادیر پیش‌فرض ====================

DEFAULT_TEXTS = {
    # دکمه‌های منوی اصلی
    "btn_buy": "🛍 خرید سرویس",
    "btn_tariffs": "🏷 تعرفه‌ها",
    "btn_my_services": "📦 سرویس‌های من",
    "btn_wallet": "💳 کیف پول",
    "btn_profile": "👤 پروفایل من",
    "btn_invite": "👥 دعوت دوستان",
    "btn_guide": "📚 آموزش اتصال",
    "btn_support": "📞 پشتیبانی",
    "btn_free_trial": "🎁 تست رایگان",

    # دکمه‌های ناوبری
    "btn_back_main": "🔙 بازگشت به منوی اصلی",
    "btn_back_services": "🔙 بازگشت به منوی سرویس‌ها",
    "btn_back": "🔙 بازگشت",
    "btn_renew": "🔄 تمدید اشتراک",
    "btn_duration_1m": "یکماهه",
    "btn_send_phone": "📱 ارسال شماره تلفن",

    # دکمه‌های کیف پول
    "btn_wallet_card": "💳 کارت به کارت",
    "btn_wallet_gateway": "🌐 پرداخت با درگاه",
    "btn_wallet_crypto": "🪙 پرداخت ارزی",
    "btn_wallet_discount": "🎁 کد تخفیف",
}


# ==================== متن‌های محتوایی پیش‌فرض ====================
# این‌ها عمداً جدا از متن سفارشی نگه داشته می‌شوند تا ادمین همیشه نسخه‌ی اصلی را ببیند.
DEFAULT_CONTENT_TEXTS = {
    "tariffs": "💰 تعرفه‌های Lidso\n\nبرای مشاهده و خرید از دکمه «خرید سرویس» استفاده کنید.\n(این متن از بات ادمین قابل ویرایشه)",
    "guide": "📚 آموزش اتصال\n\nبه زودی آموزش کامل قرار می‌گیرد.\n(این متن از بات ادمین قابل ویرایشه)",
    "support_id": "@your_support_username",
    "card_number": "6037-XXXX-XXXX-XXXX",
    "card_holder": "نام صاحب حساب",
    "crypto_address": "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX (TRC20)",
    "welcome": "👋 به ربات فروش Lidso خوش اومدی!\nاز منوی پایین یکی از گزینه‌ها رو انتخاب کن.\n\nسلام {name} عزیز 👋",
    "order_processing_text": "⏳ سفارش شما در حال آماده‌سازیه، چند لحظه صبر کنید...",
    "trial_processing_text": "⏳ در حال ساخت سرویس تست رایگان شما، چند لحظه صبر کنید...",
}

LEGACY_DEFAULTS = {
    "tariffs": "💰 تعرفه‌های Lidso\n\nبرای مشاهده و خرید از دکمه «خرید سرویس» استفاده کنید.\n(این متن از بات ادمین قابل ویرایشه)",
    "guide": "📚 آموزش اتصال\n\nبه زودی آموزش کامل قرار می‌گیرد.\n(این متن از بات ادمین قابل ویرایشه)",
    "welcome": "👋 به ربات فروش Lidso خوش اومدی!\nاز منوی پایین یکی از گزینه‌ها رو انتخاب کن.",
    "order_processing_text": "⏳ سفارش شما در حال آماده‌سازیه، چند لحظه صبر کنید...",
}

# ==================== کش داخل حافظه (برای جلوگیری از کوئری زیاد) ====================

_cache: dict[str, str] = {}


async def get_text(key: str) -> str:
    """متن دکمه/کلید کوتاه؛ برای دکمه‌ها فقط سفارشی یا پیش‌فرض نمایش داده می‌شود."""
    if key in _cache:
        return _cache[key]
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
    text = (row.value if row and row.value else None) or (row.default_value if row and row.default_value else None) or DEFAULT_TEXTS.get(key, key)
    _cache[key] = text
    return text


def _format_text(text: str, variables: dict) -> str:
    if not text:
        return text
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        return text


async def get_managed_text(key: str, fallback: str = "", **variables) -> str:
    """متن قابل مدیریت از پنل ادمین.

    اگر متن سفارشی وجود داشته باشد، ادمین می‌تواند پیش‌فرض را روشن/خاموش و قبل/بعد انتخاب کند.
    این تابع برای متن‌های پیام است، نه متن دکمه‌ها.
    """
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
    default = (row.default_value if row and row.default_value else None) or DEFAULT_CONTENT_TEXTS.get(key) or fallback
    custom = row.value if row and row.value else ""
    use_default = bool(row.use_default) if row and row.use_default is not None else True
    position = (row.default_position if row else "before") or "before"
    if custom and use_default:
        text = f"{default}\n\n{custom}" if position == "before" else f"{custom}\n\n{default}"
    elif custom:
        text = custom
    else:
        text = default if use_default else ""
    return _format_text(text, variables)


async def get_managed_text_and_entities(key: str, fallback: str = "", **variables):
    """نسخه‌ی managed برای پیام‌هایی که entities تلگرام دارند."""
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
    default = (row.default_value if row and row.default_value else None) or DEFAULT_CONTENT_TEXTS.get(key) or fallback
    custom = row.value if row and row.value else ""
    use_default = bool(row.use_default) if row and row.use_default is not None else True
    position = (row.default_position if row else "before") or "before"
    default = _format_text(default, variables)
    custom = _format_text(custom, variables)
    custom_entities = deserialize_entities(row.entities) if row else None
    if custom and use_default:
        if position == "before":
            text = f"{default}\n\n{custom}"
            shift = len(default) + 2
        else:
            text = f"{custom}\n\n{default}"
            shift = 0
        if custom_entities and shift:
            shifted = []
            for ent in custom_entities:
                data = ent.model_dump()
                if data.get("offset") is not None:
                    data["offset"] = data["offset"] + shift
                shifted.append(MessageEntity(**data))
            custom_entities = shifted
        return text, custom_entities
    if custom:
        return custom, custom_entities
    return (default if use_default else ""), None


async def get_texts(keys) -> dict:
    return {k: await get_text(k) for k in keys}


def invalidate_cache(key: str = None):
    if key:
        _cache.pop(key, None)
        _style_cache.pop(key, None)
    else:
        _cache.clear()
        _style_cache.clear()


# ==================== استایل دکمه‌ها (آیکون ایموجی پرمیوم + رنگ - Bot API 9.4+) ====================
# قابل استفاده روی هر کلید دکمه‌ای (نه فقط منوی اصلی) - کارت به کارت، بازگشت، تمدید و ...

_style_cache: dict[str, tuple] = {}


async def get_button_style(key: str) -> tuple:
    """برمی‌گردونه (icon_custom_emoji_id, style) - هرکدوم که تنظیم نشده باشه None میشه"""
    if key in _style_cache:
        return _style_cache[key]
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
    result = (row.icon_custom_emoji_id, row.style) if row else (None, None)
    _style_cache[key] = result
    return result


async def styled_button_kwargs(key: str) -> dict:
    """kwargs آماده برای ساخت KeyboardButton/InlineKeyboardButton با متن+آیکون+رنگ"""
    text = await get_text(key)
    icon, style = await get_button_style(key)
    kwargs = {"text": text}
    if icon:
        kwargs["icon_custom_emoji_id"] = icon
    if style:
        kwargs["style"] = style
    return kwargs


async def seed_ui_texts():
    """متن‌های دکمه و متن‌های محتوایی را idempotent seed و برای ویرایش مدیریت می‌کند."""
    async with async_session() as session:
        for k, v in DEFAULT_TEXTS.items():
            existing = await session.scalar(select(BotContent).where(BotContent.key == k))
            if not existing:
                session.add(BotContent(key=k, value=None, default_value=v, use_default=True, default_position="before"))
            else:
                if not existing.default_value:
                    existing.default_value = v
                if existing.value == v or existing.value == LEGACY_DEFAULTS.get(k):
                    existing.value = None
        for k, v in DEFAULT_CONTENT_TEXTS.items():
            existing = await session.scalar(select(BotContent).where(BotContent.key == k))
            if not existing:
                session.add(BotContent(key=k, value=None, default_value=v, use_default=True, default_position="before"))
            else:
                if not existing.default_value:
                    existing.default_value = v
                if existing.value == v or existing.value == LEGACY_DEFAULTS.get(k):
                    existing.value = None
        await session.commit()


async def reset_all_ui_texts():
    """بازگرداندن همه‌ی متن‌ها به حالت پیش‌فرض، بدون حذف رکوردها."""
    async with async_session() as session:
        all_defaults = {**DEFAULT_TEXTS, **DEFAULT_CONTENT_TEXTS}
        for k, v in all_defaults.items():
            row = await session.scalar(select(BotContent).where(BotContent.key == k))
            if row:
                row.value = None
                row.default_value = v
                row.use_default = True
                row.default_position = "before"
                row.entities = None
            else:
                session.add(BotContent(key=k, value=None, default_value=v, use_default=True, default_position="before"))
        await session.commit()
    invalidate_cache()


# ==================== متن‌های پیام (با پشتیبانی از entities برای حفظ ایموجی پرمیوم/بولد و ...) ====================
# توجه: entities فقط برای متن پیام‌ها معنی داره؛ روی دکمه‌های کیبورد تلگرام اصلاً پشتیبانی نمیشه.

def serialize_entities(entities):
    if not entities:
        return None
    return json.dumps([e.model_dump(mode="json") for e in entities])


def deserialize_entities(raw: str):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return [MessageEntity(**e) for e in data]
    except Exception:
        return None


async def get_content_and_entities(key: str, default: str = ""):
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
    if not row:
        return default, None
    return row.value, deserialize_entities(row.entities)


from aiogram.filters import BaseFilter


class ButtonText(BaseFilter):
    """
    فیلتر داینامیک برای دکمه‌ها. به‌جای F.text == "متن ثابت"، از این استفاده می‌کنیم:
    @router.message(ButtonText("btn_buy"))
    این‌جوری وقتی از /admin متن دکمه رو عوض کنی، تشخیص کلیک هم خودکار به‌روز میشه.

    ⚠️ حتماً باید از BaseFilter ارث ببره - اگه یه callable ساده (async def __call__ روی یک
    کلاس معمولی) باشه، aiogram متوجه async بودنش نمیشه و بدون await صداش می‌زنه؛ در اون حالت
    نتیجه همیشه یک coroutine (که همیشه truthy است) برمی‌گرده و فیلتر همیشه "قبول" میشه -
    یعنی اولین هندلری که این فیلتر رو داره همه‌چیز رو می‌قاپه (این دقیقاً باگی بود که پیش اومد).
    """
    def __init__(self, key: str):
        self.key = key

    async def __call__(self, message) -> bool:
        current_text = await get_text(self.key)
        return message.text == current_text


class AnyButtonText(BaseFilter):
    """مثل ButtonText ولی برای چند کلید هم‌زمان (OR) - مثلا وقتی چند دکمه یک هندلر مشترک دارن"""
    def __init__(self, *keys: str):
        self.keys = keys

    async def __call__(self, message) -> bool:
        current_texts = {await get_text(k) for k in self.keys}
        return message.text in current_texts
