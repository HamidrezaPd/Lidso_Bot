from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def admin_main_menu_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📁 دسته‌بندی‌ها", callback_data="admin_categories"),
                InlineKeyboardButton(text="🛍 سرویس‌ها و قیمت‌ها", callback_data="admin_services"),
            ],
            [
                InlineKeyboardButton(text="🎟 کدهای تخفیف", callback_data="admin_discounts"),
                InlineKeyboardButton(text="📦 انبار کانفیگ", callback_data="admin_stock"),
            ],
            [
                InlineKeyboardButton(text="🖥 پنل‌ها", callback_data="admin_panels"),
                InlineKeyboardButton(text="⚙️ ویرایش متن‌ها", callback_data="admin_settings"),
            ],
            [
                InlineKeyboardButton(text="📊 آمار کلی", callback_data="admin_stats"),
                InlineKeyboardButton(text="👥 لیست کاربران", callback_data="admin_users_0"),
            ],
            [
                InlineKeyboardButton(text="🔍 مدیریت سرویس‌های یک کاربر", callback_data="admin_find_user"),
                InlineKeyboardButton(text="📩 پیام به یک کاربر", callback_data="admin_message_user"),
            ],
            [
                InlineKeyboardButton(text="💰 شارژ/برداشت دستی کیف پول", callback_data="admin_wallet_adjust"),
                InlineKeyboardButton(text="🔗 ادغام ساب", callback_data="admin_submerge"),
            ],
            [
                InlineKeyboardButton(text="🎁 تست رایگان", callback_data="admin_trials"),
            ],
            [
                InlineKeyboardButton(text="🌐 تنظیمات درگاه پرداخت", callback_data="admin_gateway"),
                InlineKeyboardButton(text="🪙 تنظیمات ولت ارزی", callback_data="admin_crypto"),
            ],
            [
                InlineKeyboardButton(text="📢 پیام همگانی", callback_data="admin_broadcast"),
                InlineKeyboardButton(text="🎛 ویرایش منوی اصلی", callback_data="admin_menu_editor"),
            ],
            [
                InlineKeyboardButton(text="بستن پنل ❌", callback_data="close_admin_panel"),
            ],
        ]
    )


def back_to_admin_main_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت به منوی ادمین", callback_data="admin_main_menu")]
        ]
    )


def tx_approve_kb(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ تایید", callback_data=f"tx_approve_{tx_id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"tx_reject_{tx_id}"),
        ]]
    )


def admin_plans_list_kb(plans, action_prefix):
    """plans: لیست ServicePlan - برای هرکدوم یه دکمه با callback_data مثل addstock_12"""
    rows = []
    for p in plans:
        label = f"{p.category} - {p.name} ({p.price:,} ت)"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{action_prefix}_{p.id}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_kb():
    keys = [
        ("tariffs", "💰 متن تعرفه‌ها"),
        ("guide", "📚 متن آموزش اتصال"),
        ("support_id", "🎧 آیدی پشتیبانی"),
        ("card_number", "💳 شماره کارت"),
        ("card_holder", "👤 نام صاحب کارت"),
        ("crypto_address", "🪙 آدرس ولت کریپتو"),
        ("welcome", "👋 متن خوش‌آمدگویی"),
    ]
    rows = [[InlineKeyboardButton(text=title, callback_data=f"editcontent_{key}")] for key, title in keys]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_broadcast_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ ارسال به همه", callback_data="broadcast_confirm"),
            InlineKeyboardButton(text="❌ لغو", callback_data="broadcast_cancel"),
        ]]
    )


def admin_categories_kb(categories):
    rows = [[InlineKeyboardButton(text=f"{c.title} ({c.prefix})", callback_data=f"catinfo_{c.id}")]
            for c in categories]
    rows.append([InlineKeyboardButton(text="➕ افزودن دسته‌بندی جدید", callback_data="category_new")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_pick_kb(categories, action_prefix):
    """برای انتخاب دسته موقع ساخت پلن جدید"""
    rows = [[InlineKeyboardButton(text=c.title, callback_data=f"{action_prefix}_{c.id}")] for c in categories]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delivery_mode_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤖 خودکار (AUTO)", callback_data="delivery_AUTO"),
        InlineKeyboardButton(text="✋ دستی (MANUAL)", callback_data="delivery_MANUAL"),
    ]])


def users_list_kb(users, offset, has_more):
    rows = []
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"admin_users_{max(offset - 20, 0)}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="➡️ بعدی", callback_data=f"admin_users_{offset + 20}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
