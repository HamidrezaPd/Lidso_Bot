from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy import select
from ui_texts import get_text, styled_button_kwargs

MAIN_KEYS = [
    "btn_buy", "btn_tariffs", "btn_free_trial", "btn_my_services", "btn_wallet",
    "btn_profile", "btn_invite", "btn_guide", "btn_support",
]


async def main_keyboard():
    from database import async_session, MenuButton, BotContent

    async with async_session() as session:
        buttons = (await session.execute(
            select(MenuButton).where(MenuButton.enabled == True).order_by(MenuButton.sort_order)
        )).scalars().all()
        columns = await session.scalar(select(BotContent.value).where(BotContent.key == "menu_columns"))

    columns = int(columns) if columns and columns.isdigit() else 2
    columns = 1 if columns < 1 else (2 if columns > 2 else columns)

    kb = []
    row = []
    for b in buttons:
        if b.is_custom:
            # دکمه‌های سفارشی کلید مشترک ندارن، استایلشون از خودِ همون سطر MenuButton میاد
            kwargs = {"text": b.label or "دکمه"}
        else:
            # دکمه‌های ثابت: متن از سیستم مرکزی میاد (BotContent) - چون همونجا هم قابل ویرایشه
            kwargs = {"text": await get_text(b.key)}

        # ⚠️ رنگ و آیکون همیشه از خودِ سطر MenuButton خونده میشه، نه از BotContent - چون
        # ویرایشگر منو (بخش «🎛 ویرایشگر منوی اصلی») دقیقاً همینجا رو می‌نویسه، چه برای
        # دکمه‌های سفارشی چه برای دکمه‌های ثابت. قبلاً برای دکمه‌های ثابت اشتباهی از
        # BotContent خونده می‌شد که هیچ‌وقت نوشته نمی‌شد، پس رنگ/آیکون اصلاً اعمال نمی‌شد.
        if b.icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = b.icon_custom_emoji_id
        if b.style:
            kwargs["style"] = b.style

        if b.full_width:
            # این دکمه همیشه سطر خودش رو مستقل و تمام‌عرض می‌گیره - هر ردیف نصفه‌ی قبلی رو
            # اول جدا می‌بندیم، بعد این دکمه رو تنها توی سطر خودش می‌ذاریم
            if row:
                kb.append(row)
                row = []
            kb.append([KeyboardButton(**kwargs)])
            continue

        row.append(KeyboardButton(**kwargs))
        if len(row) == columns:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    if not kb:  # اگه همه دکمه‌ها غیرفعال شده باشن، حداقل یه چیزی نشون بده که خالی نمونه
        kb = [[KeyboardButton(text="🏠 منو")]]

    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def back_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(**await styled_button_kwargs("btn_back_main"))]],
                                resize_keyboard=True)


def _row_button_kwargs(obj, text: str) -> dict:
    """برای دکمه‌های داینامیک (دسته‌بندی/پلن) که استایل مستقیم روی خودِ رکورد دیتابیسشونه"""
    kwargs = {"text": text}
    if getattr(obj, "icon_custom_emoji_id", None):
        kwargs["icon_custom_emoji_id"] = obj.icon_custom_emoji_id
    if getattr(obj, "style", None):
        kwargs["style"] = obj.style
    return kwargs


async def services_menu_keyboard(categories):
    """categories: لیست Category از دیتابیس - کاملاً داینامیک، هرچقدر دسته اضافه کنی همینجا میاد"""
    kb = [[KeyboardButton(**_row_button_kwargs(c, c.title))] for c in categories]
    kb.append([KeyboardButton(**await styled_button_kwargs("btn_renew"))])
    kb.append([KeyboardButton(**await styled_button_kwargs("btn_back_main"))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def durations_keyboard(durations):
    """durations: لیست CategoryDuration مربوط به یک دسته خاص - کاملاً داینامیک"""
    back_kwargs = await styled_button_kwargs("btn_back_services")
    kb = [[KeyboardButton(text=d.label)] for d in durations]
    kb.append([KeyboardButton(**back_kwargs)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def _duration_label(days: int) -> str:
    if days == 0:
        return "نامحدود ♾"
    if days % 30 == 0:
        months = days // 30
        return "یک‌ماهه" if months == 1 else f"{months} ماهه"
    return f"{days} روزه"


async def plans_keyboard(plans):
    """plans: لیست ServicePlan از دیتابیس برای یک دسته خاص - قیمت‌ها همیشه به‌روز از دیتابیسه"""
    kb = [[KeyboardButton(**_row_button_kwargs(p, f"{p.name} | {_duration_label(p.duration_days)} | {p.price:,} تومان"))]
          for p in plans]
    kb.append([KeyboardButton(**await styled_button_kwargs("btn_back_services"))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def wallet_menu_keyboard():
    from database import async_session, BotContent

    async with async_session() as session:
        rows = (await session.execute(
            select(BotContent.key, BotContent.value).where(
                BotContent.key.in_(["payment_method_card_enabled", "payment_method_gateway_enabled",
                                     "payment_method_crypto_enabled"])
            )
        )).all()
    flags = {k: v for k, v in rows}
    card_on = flags.get("payment_method_card_enabled", "1") != "0"
    gateway_on = flags.get("payment_method_gateway_enabled", "1") != "0"
    crypto_on = flags.get("payment_method_crypto_enabled", "1") != "0"

    kb = []
    if card_on:
        kb.append([KeyboardButton(**await styled_button_kwargs("btn_wallet_card"))])
    method_row = []
    if gateway_on:
        method_row.append(KeyboardButton(**await styled_button_kwargs("btn_wallet_gateway")))
    if crypto_on:
        method_row.append(KeyboardButton(**await styled_button_kwargs("btn_wallet_crypto")))
    if method_row:
        kb.append(method_row)
    kb.append([KeyboardButton(**await styled_button_kwargs("btn_wallet_discount")),
               KeyboardButton(**await styled_button_kwargs("btn_support"))])
    kb.append([KeyboardButton(**await styled_button_kwargs("btn_back_main"))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def request_phone_keyboard():
    phone_kwargs = await styled_button_kwargs("btn_send_phone")
    phone_kwargs["request_contact"] = True
    kb = [
        [KeyboardButton(**phone_kwargs)],
        [KeyboardButton(**await styled_button_kwargs("btn_back"))],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def back_to_wallet_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(**await styled_button_kwargs("btn_back"))]],
                                resize_keyboard=True)


async def renew_keyboard():
    kb = [
        [KeyboardButton(**await styled_button_kwargs("btn_support"))],
        [KeyboardButton(**await styled_button_kwargs("btn_back_main"))],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
