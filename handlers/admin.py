from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from database import (
    async_session, User, ServicePlan, StockConfig, Panel, DiscountCode,
    WalletTransaction, ServiceOrder, BotContent, Category, MenuButton, SubMergeConfig, CategoryDuration,
    PaymentGatewayConfig, CryptoConfig, TrialPlan,
)
from keyboards.admin_kb import (
    admin_main_menu_kb, back_to_admin_main_kb, admin_plans_list_kb,
    confirm_broadcast_kb, admin_categories_kb,
    categories_pick_kb, delivery_mode_kb, users_list_kb, tx_reject_reason_kb,
)
from panels import get_next_config_name, volume_tag_from_plan, delete_panel_account
from states.admin_states import (
    DiscountStates, StockAddStates, PanelAddStates, ServiceEditStates, CategoryEditStates,
    SettingsEditStates, BroadcastStates, DeliverStates,
    CategoryAddStates, PlanAddStates, MenuEditStates, FindUserStates,
    SubMergeStates, MessageUserStates, WalletAdjustStates, PanelGroupsStates, PanelMarzbanStates,
    CategoryDurationStates, GatewayConfigStates, CryptoConfigStates, TrialAddStates, TrialEditStates,
    UserSearchStates, RejectReceiptStates,
)
import config as cfg

router = Router()
router.message.filter(F.from_user.id.in_(cfg.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(cfg.ADMIN_IDS))


# ==================== ورودی پنل ادمین ====================

@router.message(F.text == "/admin")
async def admin_panel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔐 پنل مدیریت ربات Lidso", reply_markup=admin_main_menu_kb())


@router.callback_query(F.data == "admin_main_menu")
async def cb_admin_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🔐 پنل مدیریت ربات Lidso", reply_markup=admin_main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "close_admin_panel")
async def cb_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# ==================== آمار ====================

@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: CallbackQuery):
    async with async_session() as session:
        users_count = await session.scalar(select(func.count(User.id)))
        orders_count = await session.scalar(select(func.count(ServiceOrder.id)))
        total_revenue = await session.scalar(select(func.coalesce(func.sum(ServiceOrder.price), 0)))
        pending_tx = await session.scalar(
            select(func.count(WalletTransaction.id)).where(WalletTransaction.status == "PENDING")
        )
        by_service = (await session.execute(
            select(
                ServiceOrder.service_name,
                func.count(ServiceOrder.id),
                func.coalesce(func.sum(ServiceOrder.price), 0),
            ).group_by(ServiceOrder.service_name).order_by(func.count(ServiceOrder.id).desc())
        )).all()

    text = (
        f"📊 آمار کلی\n\n"
        f"👥 تعداد کاربران: {users_count}\n"
        f"🛍 تعداد سفارش‌ها: {orders_count}\n"
        f"💰 مجموع فروش: {total_revenue:,} تومان\n"
        f"⏳ تراکنش‌های در انتظار تایید: {pending_tx}\n"
    )
    if by_service:
        text += "\n📦 فروش به تفکیک سرویس:\n"
        for name, count, revenue in by_service:
            text += f"• {name}: {count} فروش — {revenue:,} تومان\n"

    await callback.message.edit_text(text, reply_markup=back_to_admin_main_kb())
    await callback.answer()


# ==================== لیست کاربران ====================

@router.callback_query(F.data.regexp(r"^admin_users_\d+$"))
async def cb_users_list(callback: CallbackQuery):
    offset = int(callback.data.split("_")[-1])
    page_size = 20

    async with async_session() as session:
        total = await session.scalar(select(func.count(User.id)))
        users = (await session.execute(
            select(User).order_by(User.id.desc()).offset(offset).limit(page_size)
        )).scalars().all()

    has_more = (offset + page_size) < total

    if not users:
        text = "هنوز هیچ کاربری وارد ربات نشده."
    else:
        lines = [f"👥 کاربران (نمایش {offset + 1} تا {offset + len(users)} از {total}):\n"]
        for u in users:
            uname = f"@{u.username}" if u.username else "بدون یوزرنیم"
            phone = f" — ☎️ {u.phone_number}" if u.phone_number else ""
            lines.append(f"🆔 {u.user_id} — {u.full_name or '-'} — {uname} — 💰 {u.balance:,} ت{phone}")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=users_list_kb(users, offset, has_more))
    await callback.answer()


# ==================== جستجوی کاربر ====================

@router.callback_query(F.data == "admin_users_search")
async def cb_users_search_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 آیدی عددی کاربر مورد نظر رو بفرست:")
    await state.set_state(UserSearchStates.waiting_user_id)
    await callback.answer()


@router.message(UserSearchStates.waiting_user_id)
async def users_search_process(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ فقط آیدی عددی (عدد) وارد کنید:")
        return

    target_id = int(text)
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == target_id))
        if not user:
            await message.answer("❌ کاربری با این آیدی پیدا نشد.")
            return
        orders_count = await session.scalar(
            select(func.count(ServiceOrder.id)).where(ServiceOrder.user_id == target_id)
        )

    uname = f"@{user.username}" if user.username else "بدون یوزرنیم"
    joined = user.joined_at.strftime("%Y-%m-%d %H:%M") if user.joined_at else "-"
    text = (
        f"👤 اطلاعات کاربر پیدا شد\n\n"
        f"🆔 آیدی عددی: {user.user_id}\n"
        f"✏️ یوزرنیم: {uname}\n"
        f"👤 نام: {user.full_name or '-'}\n"
        f"💰 موجودی: {user.balance:,} تومان\n"
        f"🛒 تعداد خرید: {user.total_purchases}\n"
        f"📦 حجم خریداری‌شده: {user.total_volume} گیگ\n"
        f"👥 دعوت‌شده‌ها: {user.referral_count} نفر\n"
        f"📋 تعداد سفارش‌ها: {orders_count}\n"
        f"📅 تاریخ ورود: {joined}"
    )
    await message.answer(text, reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== کدهای تخفیف ====================

@router.callback_query(F.data == "admin_discounts")
async def cb_discounts(callback: CallbackQuery):
    async with async_session() as session:
        codes = (await session.execute(select(DiscountCode))).scalars().all()
    text = "🎟 کدهای تخفیف:\n\n"
    rows = []
    if codes:
        for c in codes:
            status = "فعال ✅" if c.active else "غیرفعال ❌"
            text += f"`{c.code}` — {c.percent}% — {c.current_uses}/{c.max_uses} استفاده — {status}\n"
            rows.append([InlineKeyboardButton(text=f"🗑 حذف {c.code}", callback_data=f"discdel_{c.id}")])
    else:
        text += "هیچ کدی ثبت نشده.\n"
    rows.append([InlineKeyboardButton(text="➕ ساخت کد جدید", callback_data="discount_new")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                                      parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("discdel_"))
async def cb_discount_delete(callback: CallbackQuery):
    code_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        code = await session.get(DiscountCode, code_id)
        if code:
            await session.delete(code)
            await session.commit()
    await cb_discounts(callback)


@router.callback_query(F.data == "discount_new")
async def discount_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("کد تخفیف را وارد کنید (مثلا OFF15):")
    await state.set_state(DiscountStates.waiting_code)
    await callback.answer()


@router.message(DiscountStates.waiting_code)
async def discount_code_in(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip().upper())
    await message.answer("درصد تخفیف را وارد کنید (مثلا 15):")
    await state.set_state(DiscountStates.waiting_percent)


@router.message(DiscountStates.waiting_percent)
async def discount_percent_in(message: Message, state: FSMContext):
    if not message.text.isdigit() or not (1 <= int(message.text) <= 100):
        await message.answer("❌ عدد بین 1 تا 100 وارد کنید:")
        return
    await state.update_data(percent=int(message.text))
    await message.answer("این کد چند بار قابل استفاده باشد؟ (مثلا 10)")
    await state.set_state(DiscountStates.waiting_max_uses)


@router.message(DiscountStates.waiting_max_uses)
async def discount_maxuses_in(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        existing = await session.scalar(select(DiscountCode).where(DiscountCode.code == data["code"]))
        if existing:
            await message.answer("❌ این کد قبلاً ثبت شده.", reply_markup=admin_main_menu_kb())
            await state.clear()
            return
        session.add(DiscountCode(code=data["code"], percent=data["percent"], max_uses=int(message.text)))
        await session.commit()
    await message.answer(
        f"✅ کد «{data['code']}» با {data['percent']}% تخفیف و {message.text} بار مجاز ساخته شد.",
        reply_markup=admin_main_menu_kb(),
    )
    await state.clear()


# ==================== سرویس‌ها و قیمت‌ها ====================

@router.callback_query(F.data == "admin_services")
async def cb_services(callback: CallbackQuery):
    async with async_session() as session:
        plans = (await session.execute(
            select(ServicePlan).order_by(ServicePlan.category, ServicePlan.sort_order)
        )).scalars().all()
    kb = admin_plans_list_kb(plans, "editplan")
    kb.inline_keyboard.insert(-1, [InlineKeyboardButton(text="➕ افزودن پلن جدید", callback_data="plan_new")])
    await callback.message.edit_text(
        "🛍 سرویس‌ها (برای ویرایش قیمت کلیک کنید):",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editplan_"))
async def cb_editplan(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        plan = await session.get(ServicePlan, plan_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش نام پلن", callback_data=f"planname_{plan_id}")],
        [InlineKeyboardButton(text="✏️ ویرایش قیمت", callback_data=f"planprice_{plan_id}")],
        [InlineKeyboardButton(text="🔢 ویرایش HWID (0=دست نزن)", callback_data=f"planhwid_{plan_id}")],
        [InlineKeyboardButton(text="⏳ ویرایش مدت زمان (0=نامحدود)", callback_data=f"planduration_{plan_id}")],
        [InlineKeyboardButton(text="🖼 تنظیم آیکون ایموجی پرمیوم", callback_data=f"planicon_{plan_id}")],
        [
            InlineKeyboardButton(text="🔵 آبی", callback_data=f"planstyle_{plan_id}_primary"),
            InlineKeyboardButton(text="🟢 سبز", callback_data=f"planstyle_{plan_id}_success"),
            InlineKeyboardButton(text="🔴 قرمز", callback_data=f"planstyle_{plan_id}_danger"),
            InlineKeyboardButton(text="⚪️ پیش‌فرض", callback_data=f"planstyle_{plan_id}_none"),
        ],
        [
            InlineKeyboardButton(text="⬆️ بالاتر", callback_data=f"planup_{plan_id}"),
            InlineKeyboardButton(text="⬇️ پایین‌تر", callback_data=f"plandown_{plan_id}"),
        ],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کردن" if plan.active else "✅ فعال کردن",
            callback_data=f"plantoggle_{plan_id}",
        )],
        [InlineKeyboardButton(text="🗑 حذف کامل این پلن", callback_data=f"plandelete_{plan_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_services")],
    ])
    duration_fa = "نامحدود ♾" if plan.duration_days == 0 else f"{plan.duration_days} روز"
    icon_status = "دارد ✅" if plan.icon_custom_emoji_id else "ندارد"
    style_fa = {"primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز"}.get(plan.style, "پیش‌فرض")
    await callback.message.edit_text(
        f"🛍 {plan.name}\n\nدسته: {plan.category}\nقیمت: {plan.price:,} تومان\n"
        f"HWID: {plan.hwid_limit if plan.hwid_limit else 'دست‌نزده (0)'}\n"
        f"مدت زمان: {duration_fa}\n"
        f"وضعیت: {'فعال ✅' if plan.active else 'غیرفعال ❌'}\n"
        f"🖼 آیکون ایموجی پرمیوم: {icon_status}\n🎨 رنگ فعلی: {style_fa}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("planduration_"))
async def cb_planduration_start(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    await state.update_data(plan_id=plan_id)
    await callback.message.answer(
        "مدت زمان جدید رو به روز وارد کن (مثلا 30، 60، 90). برای نامحدود، عدد 0 رو بفرست:"
    )
    await state.set_state(ServiceEditStates.waiting_new_duration)
    await callback.answer()


@router.message(ServiceEditStates.waiting_new_duration)
async def process_new_duration(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید (0 برای نامحدود):")
        return
    data = await state.get_data()
    async with async_session() as session:
        plan = await session.get(ServicePlan, data["plan_id"])
        plan.duration_days = int(message.text.strip())
        await session.commit()
        plan_name, duration_days = plan.name, plan.duration_days
    duration_fa = "نامحدود ♾" if duration_days == 0 else f"{duration_days} روز"
    await message.answer(f"✅ مدت زمان پلن «{plan_name}» به «{duration_fa}» تغییر کرد.",
                          reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("planhwid_"))
async def cb_planhwid(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    await state.update_data(plan_id=plan_id)
    await callback.message.answer("مقدار جدید HWID رو وارد کن (0 = دست نزن):")
    await state.set_state(ServiceEditStates.waiting_new_hwid)
    await callback.answer()


@router.message(ServiceEditStates.waiting_new_hwid)
async def process_new_hwid(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید (0 = دست نزن):")
        return
    data = await state.get_data()
    async with async_session() as session:
        plan = await session.get(ServicePlan, data["plan_id"])
        new_hwid = int(message.text.strip())
        plan.hwid_limit = new_hwid

        # برای پلن‌های Unlimited، تعداد کاربر واقعی باید با HWID یکی باشد.
        # این مقدار توسط volume_tag_from_plan و PhantomHubs هم استفاده می‌شود.
        if new_hwid > 0 and (plan.category == "LidsoUnlimited" or plan.volume_gb == 0):
            plan.max_users = new_hwid

        await session.commit()
        plan_name = plan.name
    await message.answer(f"✅ HWID پلن «{plan_name}» به‌روزرسانی شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("plandelete_"))
async def cb_plandelete(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"plandeleteconfirm_{plan_id}"),
        InlineKeyboardButton(text="❌ انصراف", callback_data=f"editplan_{plan_id}"),
    ]])
    await callback.message.edit_text(
        "⚠️ مطمئنی؟ این پلن کاملاً حذف میشه (سفارش‌های قبلی این پلن دست‌نخورده باقی می‌مونن، "
        "ولی دیگه قابل خرید نیست و از انبار/تنظیمات هم پاک میشه).",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plandeleteconfirm_"))
async def cb_plandelete_confirm(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        plan = await session.get(ServicePlan, plan_id)
        if plan:
            await session.delete(plan)
            await session.commit()
    await callback.message.edit_text("✅ پلن حذف شد.", reply_markup=admin_main_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("planname_"))
async def cb_planname(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    await state.update_data(plan_id=plan_id)
    await callback.message.answer("نام جدید پلن را وارد کنید:")
    await state.set_state(ServiceEditStates.waiting_new_name)
    await callback.answer()


@router.message(ServiceEditStates.waiting_new_name)
async def process_new_name(message: Message, state: FSMContext):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("❌ نام نمی‌تواند خالی باشد. دوباره وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        plan = await session.get(ServicePlan, data["plan_id"])
        old_name = plan.name
        plan.name = new_name
        await session.commit()
    await message.answer(f"✅ نام پلن از «{old_name}» به «{new_name}» تغییر کرد.",
                          reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("planprice_"))
async def cb_planprice(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    await state.update_data(plan_id=plan_id)
    await callback.message.answer("قیمت جدید را به تومان وارد کنید:")
    await state.set_state(ServiceEditStates.waiting_new_price)
    await callback.answer()


@router.callback_query(F.data.startswith("plantoggle_"))
async def cb_plantoggle(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        plan = await session.get(ServicePlan, plan_id)
        plan.active = not plan.active
        await session.commit()
    await cb_editplan(callback)


@router.callback_query(F.data.startswith("planup_") | F.data.startswith("plandown_"))
async def cb_plan_move(callback: CallbackQuery):
    direction_up = callback.data.startswith("planup_")
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        target = await session.get(ServicePlan, plan_id)
        siblings = (await session.execute(
            select(ServicePlan).where(ServicePlan.category == target.category).order_by(ServicePlan.sort_order)
        )).scalars().all()
        idx = next((i for i, p in enumerate(siblings) if p.id == plan_id), None)
        if idx is None:
            await callback.answer()
            return
        swap_idx = idx - 1 if direction_up else idx + 1
        if 0 <= swap_idx < len(siblings):
            siblings[idx].sort_order, siblings[swap_idx].sort_order = (
                siblings[swap_idx].sort_order, siblings[idx].sort_order
            )
            await session.commit()
    await cb_editplan(callback)


@router.message(ServiceEditStates.waiting_new_price)
async def process_new_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        plan = await session.get(ServicePlan, data["plan_id"])
        plan.price = int(message.text)
        await session.commit()
        plan_name = plan.name
    await message.answer(f"✅ قیمت «{plan_name}» به {int(message.text):,} تومان تغییر کرد.",
                          reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== دسته‌بندی‌ها (Category) ====================

@router.callback_query(F.data == "admin_categories")
async def cb_categories(callback: CallbackQuery):
    async with async_session() as session:
        categories = (await session.execute(
            select(Category).order_by(Category.sort_order)
        )).scalars().all()
    text = "📁 دسته‌بندی‌های فعلی:\n\n" + (
        "\n".join([f"• {c.title} — پیشوند: {c.prefix} — {'فعال ✅' if c.active else 'غیرفعال ❌'}"
                   for c in categories]) or "هنوز دسته‌ای ثبت نشده."
    )
    await callback.message.edit_text(text, reply_markup=admin_categories_kb(categories))
    await callback.answer()


@router.callback_query(F.data == "category_new")
async def category_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "متن دکمه دسته‌بندی جدید را وارد کنید.\n"
        "مثال: `Lidso VIP | لیدسو وی‌آی‌پی`",
        parse_mode="Markdown",
    )
    await state.set_state(CategoryAddStates.waiting_title)
    await callback.answer()


@router.message(CategoryAddStates.waiting_title)
async def category_title_in(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer(
        "حالا یک «پیشوند انگلیسی بدون فاصله» برای این دسته وارد کنید (برای اسم‌گذاری کانفیگ‌ها).\n"
        "مثال: `LidsoVIP`",
        parse_mode="Markdown",
    )
    await state.set_state(CategoryAddStates.waiting_prefix)


@router.message(CategoryAddStates.waiting_prefix)
async def category_prefix_in(message: Message, state: FSMContext):
    prefix = message.text.strip().replace(" ", "")
    data = await state.get_data()
    async with async_session() as session:
        existing = await session.scalar(select(Category).where(Category.prefix == prefix))
        if existing:
            await message.answer("❌ این پیشوند قبلاً استفاده شده. یک پیشوند دیگر وارد کنید:")
            return
        session.add(Category(title=data["title"], prefix=prefix))
        await session.commit()
    await message.answer(
        f"✅ دسته‌بندی «{data['title']}» ساخته شد.\n\n"
        f"حالا از «🛍 سرویس‌ها و قیمت‌ها» → «➕ افزودن پلن جدید» می‌تونی پلن‌هاشو اضافه کنی.",
        reply_markup=admin_main_menu_kb(),
    )
    await state.clear()


@router.callback_query(F.data.startswith("catinfo_"))
async def cb_category_info(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        plan_count = await session.scalar(
            select(func.count(ServicePlan.id)).where(ServicePlan.category == category.prefix)
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش نام دسته‌بندی", callback_data=f"cattitle_{cat_id}")],
        [InlineKeyboardButton(text="✏️ ویرایش پیشوند (prefix)", callback_data=f"catprefix_{cat_id}")],
        [InlineKeyboardButton(text="🖼 تنظیم آیکون ایموجی پرمیوم", callback_data=f"catticon_{cat_id}")],
        [
            InlineKeyboardButton(text="🔵 آبی", callback_data=f"catstyle_{cat_id}_primary"),
            InlineKeyboardButton(text="🟢 سبز", callback_data=f"catstyle_{cat_id}_success"),
            InlineKeyboardButton(text="🔴 قرمز", callback_data=f"catstyle_{cat_id}_danger"),
            InlineKeyboardButton(text="⚪️ پیش‌فرض", callback_data=f"catstyle_{cat_id}_none"),
        ],
        [InlineKeyboardButton(text="⏳ مدیریت مدت‌زمان‌های این دسته", callback_data=f"catdurations_{cat_id}")],
        [
            InlineKeyboardButton(text="⬆️ بالاتر", callback_data=f"catup_{cat_id}"),
            InlineKeyboardButton(text="⬇️ پایین‌تر", callback_data=f"catdown_{cat_id}"),
        ],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کردن" if category.active else "✅ فعال کردن",
            callback_data=f"cattoggle_{cat_id}",
        )],
        [InlineKeyboardButton(text="🗑 حذف کامل این سرویس", callback_data=f"catdelete_{cat_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_categories")],
    ])
    icon_status = "دارد ✅" if category.icon_custom_emoji_id else "ندارد"
    style_fa = {"primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز"}.get(category.style, "پیش‌فرض")
    await callback.message.edit_text(
        f"📁 {category.title}\n\nپیشوند: {category.prefix}\nتعداد پلن‌ها: {plan_count}\n"
        f"وضعیت: {'فعال ✅' if category.active else 'غیرفعال ❌'}\n"
        f"🖼 آیکون ایموجی پرمیوم: {icon_status}\n🎨 رنگ فعلی: {style_fa}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cattitle_"))
async def cb_cattitle(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(cat_id=cat_id)
    await callback.message.answer("نام جدید دسته‌بندی را وارد کنید:")
    await state.set_state(CategoryEditStates.waiting_new_title)
    await callback.answer()


@router.message(CategoryEditStates.waiting_new_title)
async def process_new_cat_title(message: Message, state: FSMContext):
    new_title = message.text.strip()
    if not new_title:
        await message.answer("❌ نام نمی‌تواند خالی باشد. دوباره وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        category = await session.get(Category, data["cat_id"])
        old_title = category.title
        category.title = new_title
        await session.commit()
    await message.answer(f"✅ نام دسته‌بندی از «{old_title}» به «{new_title}» تغییر کرد.",
                          reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("catprefix_"))
async def cb_catprefix(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(cat_id=cat_id)
    await callback.message.answer(
        "⚠️ پیشوند (prefix) توی نام‌گذاری خودکار کانفیگ‌ها استفاده میشه (مثلاً LidsoPrime).\n"
        "تغییرش فقط روی کانفیگ‌های جدید اثر می‌ذاره؛ کانفیگ‌های قبلی با پیشوند قدیمی می‌مونن.\n\n"
        "پیشوند جدید را وارد کنید (فقط حروف انگلیسی/عدد، بدون فاصله):"
    )
    await state.set_state(CategoryEditStates.waiting_new_prefix)
    await callback.answer()


@router.message(CategoryEditStates.waiting_new_prefix)
async def process_new_cat_prefix(message: Message, state: FSMContext):
    new_prefix = message.text.strip()
    if not new_prefix or not new_prefix.replace("_", "").isalnum():
        await message.answer("❌ پیشوند فقط می‌تواند حروف انگلیسی/عدد/آندرلاین باشد. دوباره وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        existing = await session.scalar(
            select(Category).where(Category.prefix == new_prefix, Category.id != data["cat_id"])
        )
        if existing:
            await message.answer("❌ این پیشوند قبلاً برای دسته‌بندی دیگری استفاده شده. یه پیشوند دیگه وارد کن:")
            return
        category = await session.get(Category, data["cat_id"])
        old_prefix = category.prefix
        category.prefix = new_prefix
        await session.commit()
    await message.answer(f"✅ پیشوند از «{old_prefix}» به «{new_prefix}» تغییر کرد.",
                          reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("catdurations_"))
async def cb_category_durations(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        durations = (await session.execute(
            select(CategoryDuration).where(CategoryDuration.category_id == cat_id).order_by(CategoryDuration.sort_order)
        )).scalars().all()

    rows = []
    for d in durations:
        status = "✅" if d.active else "🚫"
        label_fa = "نامحدود ♾" if d.days == 0 else f"{d.days} روز"
        rows.append([InlineKeyboardButton(text=f"{status} {d.label} ({label_fa})", callback_data=f"catdurinfo_{d.id}")])
    rows.append([InlineKeyboardButton(text="➕ افزودن مدت‌زمان جدید", callback_data=f"catdurnew_{cat_id}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"catinfo_{cat_id}")])

    await callback.message.edit_text(
        f"⏳ مدت‌زمان‌های «{category.title}»:\n\n(روی هرکدوم بزن برای جابه‌جایی/حذف)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("catdurnew_"))
async def cb_category_duration_new(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(new_cat_duration_category_id=cat_id)
    await callback.message.answer("اسم این مدت‌زمان رو وارد کن (مثلا «سه ماهه» یا «نامحدود»):")
    await state.set_state(CategoryDurationStates.waiting_label)
    await callback.answer()


@router.message(CategoryDurationStates.waiting_label)
async def category_duration_label_in(message: Message, state: FSMContext):
    await state.update_data(cat_duration_label=message.text.strip())
    await message.answer("چند روزه باشه؟ برای نامحدود عدد 0 رو بفرست:")
    await state.set_state(CategoryDurationStates.waiting_days)


@router.message(CategoryDurationStates.waiting_days)
async def category_duration_days_in(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید (0 برای نامحدود):")
        return
    data = await state.get_data()
    async with async_session() as session:
        session.add(CategoryDuration(
            category_id=data["new_cat_duration_category_id"],
            label=data["cat_duration_label"], days=int(message.text.strip()),
        ))
        await session.commit()
    await message.answer(f"✅ مدت‌زمان «{data['cat_duration_label']}» اضافه شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("catdurinfo_"))
async def cb_category_duration_info(callback: CallbackQuery):
    dur_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        dur = await session.get(CategoryDuration, dur_id)
        plan_count = await session.scalar(select(func.count(ServicePlan.id)).where(ServicePlan.duration_id == dur_id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬆️ بالاتر", callback_data=f"catdurup_{dur_id}"),
            InlineKeyboardButton(text="⬇️ پایین‌تر", callback_data=f"catdurdown_{dur_id}"),
        ],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کن" if dur.active else "✅ فعال کن",
            callback_data=f"catdurtoggle_{dur_id}",
        )],
        [InlineKeyboardButton(text="🗑 حذف این مدت‌زمان", callback_data=f"catdurdelete_{dur_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"catdurations_{dur.category_id}")],
    ])
    label_fa = "نامحدود ♾" if dur.days == 0 else f"{dur.days} روز"
    await callback.message.edit_text(
        f"⏳ {dur.label} ({label_fa})\n\nتعداد پلن‌های این مدت‌زمان: {plan_count}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("catdurtoggle_"))
async def cb_category_duration_toggle(callback: CallbackQuery):
    dur_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        dur = await session.get(CategoryDuration, dur_id)
        dur.active = not dur.active
        await session.commit()
    await cb_category_duration_info(callback)


@router.callback_query(F.data.startswith("catdurup_") | F.data.startswith("catdurdown_"))
async def cb_category_duration_move(callback: CallbackQuery):
    direction_up = callback.data.startswith("catdurup_")
    dur_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        target = await session.get(CategoryDuration, dur_id)
        siblings = (await session.execute(
            select(CategoryDuration).where(CategoryDuration.category_id == target.category_id)
            .order_by(CategoryDuration.sort_order)
        )).scalars().all()
        idx = next((i for i, d in enumerate(siblings) if d.id == dur_id), None)
        if idx is None:
            await callback.answer()
            return
        swap_idx = idx - 1 if direction_up else idx + 1
        if 0 <= swap_idx < len(siblings):
            siblings[idx].sort_order, siblings[swap_idx].sort_order = (
                siblings[swap_idx].sort_order, siblings[idx].sort_order
            )
            await session.commit()
    await cb_category_duration_info(callback)


@router.callback_query(F.data.startswith("catdurdelete_"))
async def cb_category_duration_delete(callback: CallbackQuery):
    dur_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        plan_count = await session.scalar(select(func.count(ServicePlan.id)).where(ServicePlan.duration_id == dur_id))
        if plan_count:
            await callback.answer(
                f"⚠️ {plan_count} پلن به این مدت‌زمان وصله. اول اون پلن‌ها رو حذف/غیرفعال کن.",
                show_alert=True,
            )
            return
        dur = await session.get(CategoryDuration, dur_id)
        category_id = dur.category_id
        await session.delete(dur)
        await session.commit()
    await callback.answer("✅ حذف شد")
    fake_callback_data = f"catdurations_{category_id}"
    callback.data = fake_callback_data
    await cb_category_durations(callback)


@router.callback_query(F.data.startswith("catdelete_"))
async def cb_category_delete(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        plan_count = await session.scalar(
            select(func.count(ServicePlan.id)).where(ServicePlan.category == category.prefix)
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ بله، همه‌چی حذف بشه", callback_data=f"catdeleteconfirm_{cat_id}"),
        InlineKeyboardButton(text="❌ انصراف", callback_data=f"catinfo_{cat_id}"),
    ]])
    await callback.message.edit_text(
        f"⚠️ مطمئنی؟ این سرویس «{category.title}» و {plan_count} پلن زیرش کاملاً حذف میشن "
        f"(سفارش‌های قبلی مشتری‌ها دست‌نخورده می‌مونن، فقط دیگه قابل خرید نیستن).",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("catdeleteconfirm_"))
async def cb_category_delete_confirm(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        if category:
            plans = (await session.execute(
                select(ServicePlan).where(ServicePlan.category == category.prefix)
            )).scalars().all()
            for p in plans:
                await session.delete(p)
            await session.delete(category)
            await session.commit()
    await callback.message.edit_text("✅ سرویس و پلن‌هاش کاملاً حذف شدن.", reply_markup=admin_main_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("cattoggle_"))
async def cb_category_toggle(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        category.active = not category.active
        await session.commit()
    await cb_category_info(callback)


@router.callback_query(F.data.startswith("catup_") | F.data.startswith("catdown_"))
async def cb_category_move(callback: CallbackQuery):
    direction_up = callback.data.startswith("catup_")
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        categories = (await session.execute(select(Category).order_by(Category.sort_order))).scalars().all()
        idx = next((i for i, c in enumerate(categories) if c.id == cat_id), None)
        if idx is None:
            await callback.answer()
            return
        swap_idx = idx - 1 if direction_up else idx + 1
        if 0 <= swap_idx < len(categories):
            categories[idx].sort_order, categories[swap_idx].sort_order = (
                categories[swap_idx].sort_order, categories[idx].sort_order
            )
            await session.commit()
    await cb_category_info(callback)


# ==================== افزودن پلن جدید ====================

@router.callback_query(F.data == "plan_new")
async def plan_new_start(callback: CallbackQuery):
    async with async_session() as session:
        categories = (await session.execute(select(Category).where(Category.active == True))).scalars().all()
    if not categories:
        await callback.message.edit_text("اول باید یک دسته‌بندی بسازید.", reply_markup=back_to_admin_main_kb())
        await callback.answer()
        return
    await callback.message.edit_text("این پلن مربوط به کدام دسته‌بندی است؟",
                                      reply_markup=categories_pick_kb(categories, "plancat"))
    await callback.answer()


@router.callback_query(F.data.startswith("plancat_"))
async def plan_new_category(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        category = await session.get(Category, cat_id)
        durations = (await session.execute(
            select(CategoryDuration).where(
                CategoryDuration.category_id == cat_id, CategoryDuration.active == True
            ).order_by(CategoryDuration.sort_order)
        )).scalars().all()

    await state.update_data(category_prefix=category.prefix, category_id=cat_id)

    rows = [[InlineKeyboardButton(text=d.label, callback_data=f"plandur_{d.id}")] for d in durations]
    rows.append([InlineKeyboardButton(text="➕ ساخت مدت‌زمان جدید", callback_data="plandur_new")])
    await callback.message.edit_text(
        f"این پلن برای دسته‌ی «{category.title}» چه مدت‌زمانی داشته باشه؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "plandur_new")
async def plan_dur_new_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "اسم این مدت‌زمان رو وارد کن (مثلا «یک ماهه»، «دو ماهه»، «نامحدود»):"
    )
    await state.set_state(PlanAddStates.waiting_new_duration_label)
    await callback.answer()


@router.message(PlanAddStates.waiting_new_duration_label)
async def plan_dur_new_label(message: Message, state: FSMContext):
    await state.update_data(new_duration_label=message.text.strip())
    await message.answer("چند روزه باشه؟ برای نامحدود عدد 0 رو بفرست:")
    await state.set_state(PlanAddStates.waiting_new_duration_days)


@router.message(PlanAddStates.waiting_new_duration_days)
async def plan_dur_new_days(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید (0 برای نامحدود):")
        return
    data = await state.get_data()
    days = int(message.text.strip())
    async with async_session() as session:
        dur = CategoryDuration(category_id=data["category_id"], label=data["new_duration_label"], days=days)
        session.add(dur)
        await session.commit()
        await session.refresh(dur)
    await state.update_data(duration_id=dur.id, duration_days=days)
    await message.answer(f"✅ مدت‌زمان «{data['new_duration_label']}» ساخته شد.\n\nاسم پلن را وارد کنید:")
    await state.set_state(PlanAddStates.waiting_name)


@router.callback_query(F.data.startswith("plandur_") & ~F.data.startswith("plandur_new"))
async def plan_dur_pick(callback: CallbackQuery, state: FSMContext):
    dur_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        dur = await session.get(CategoryDuration, dur_id)
    await state.update_data(duration_id=dur.id, duration_days=dur.days)
    await callback.message.answer("اسم پلن را وارد کنید (مثلا «10 گیگ وی‌آی‌پی» یا «1 کاربره وی‌آی‌پی»):")
    await state.set_state(PlanAddStates.waiting_name)
    await callback.answer()


@router.message(PlanAddStates.waiting_name)
async def plan_new_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("حجم به گیگابایت چقدر باشه؟ (اگه سرویس نامحدود/کاربر-محوره، عدد 0 رو بفرست)")
    await state.set_state(PlanAddStates.waiting_volume)


@router.message(PlanAddStates.waiting_volume)
async def plan_new_volume(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    await state.update_data(volume_gb=int(message.text.strip()))
    await message.answer("قیمت را به تومان وارد کنید:")
    await state.set_state(PlanAddStates.waiting_price)


@router.message(PlanAddStates.waiting_price)
async def plan_new_price(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید:")
        return
    await state.update_data(price=int(message.text.strip()))
    await message.answer("نحوه تحویل این پلن چطور باشه؟", reply_markup=delivery_mode_kb())
    await state.set_state(PlanAddStates.waiting_delivery_mode)


@router.callback_query(PlanAddStates.waiting_delivery_mode, F.data.startswith("delivery_"))
async def plan_new_delivery(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split("_")[1]  # AUTO یا MANUAL
    await state.update_data(delivery_mode=mode)
    await callback.message.answer(
        "🔢 محدودیت تعداد دستگاه (HWID) رو وارد کن:\n"
        "عدد 0 = کاری به این تنظیم نداشته باش (دست نزن)\n"
        "هر عدد دیگه‌ای = همون رو برای این پلن روی پنل تنظیم کن"
    )
    await state.set_state(PlanAddStates.waiting_hwid)
    await callback.answer()


@router.message(PlanAddStates.waiting_hwid)
async def plan_new_hwid(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد وارد کنید (0 یعنی دست نزن):")
        return
    hwid_limit = int(message.text.strip())
    data = await state.get_data()

    # در پلن‌های Unlimited، HWID همان تعداد کاربر مجاز است.
    # قبلاً max_users همیشه 1 ذخیره می‌شد و باعث می‌شد پلن 2 کاربره
    # هنگام ساخت نام و ارسال به PhantomHubs به 1user تبدیل شود.
    is_unlimited = data["category_prefix"] == "LidsoUnlimited" or data["volume_gb"] == 0
    max_users = hwid_limit if is_unlimited and hwid_limit > 0 else 1

    async with async_session() as session:
        session.add(ServicePlan(
            category=data["category_prefix"], name=data["name"], volume_gb=data["volume_gb"],
            price=data["price"], duration_id=data["duration_id"], duration_days=data["duration_days"],
            delivery_mode=data["delivery_mode"], max_users=max_users, hwid_limit=hwid_limit,
        ))
        await session.commit()
    duration_fa = "نامحدود ♾" if data["duration_days"] == 0 else f"{data['duration_days']} روزه"
    await message.answer(
        f"✅ پلن «{data['name']}» با قیمت {data['price']:,} تومان و مدت {duration_fa} ساخته شد.\n\n"
        + ("حالا از «🖥 پنل‌ها» این پلن رو به یک پنل وصل کن." if data["delivery_mode"] == "AUTO"
           else "چون تحویل دستیه، نیازی به وصل کردن پنل نیست."),
        reply_markup=admin_main_menu_kb(),
    )
    await state.clear()


# ==================== انبار کانفیگ ====================

@router.callback_query(F.data == "admin_stock")
async def cb_stock(callback: CallbackQuery):
    async with async_session() as session:
        plans = (await session.execute(select(ServicePlan).where(ServicePlan.active == True))).scalars().all()
        rows_data = []
        for p in plans:
            count = await session.scalar(
                select(func.count(StockConfig.id)).where(
                    StockConfig.service_id == p.id, StockConfig.status == "AVAILABLE"
                )
            )
            rows_data.append((p, count))

    text = "📦 انبار کانفیگ (تعداد موجودی هر سرویس):\n\n"
    text += "\n".join([f"• {p.category} - {p.name}: {c} عدد" for p, c in rows_data]) or "سرویسی وجود ندارد."

    kb_rows = [[InlineKeyboardButton(text=f"➕ افزودن به «{p.name}»", callback_data=f"addstock_{p.id}")]
               for p, _ in rows_data]
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data.startswith("addstock_"))
async def cb_addstock(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        plan = await session.get(ServicePlan, plan_id)
        panel = await session.get(Panel, plan.panel_id) if plan.panel_id else None

    volume_tag = volume_tag_from_plan(plan)
    suggested_name = await get_next_config_name(plan.category, volume_tag, panel)
    await state.update_data(plan_id=plan_id, suggested_name=suggested_name)

    await callback.message.answer(
        f"نام کانفیگ پیشنهادی (برای سینک ماندن شماره‌ها): `{suggested_name}`\n\n"
        f"اگه این کانفیگ رو دستی توی پنل با همین اسم ساختی، فقط بنویس «تایید».\n"
        f"یا اگه اسم دیگه‌ای ساختی، همون رو بفرست:",
        parse_mode="Markdown",
    )
    await state.set_state(StockAddStates.waiting_config_name)
    await callback.answer()


@router.message(StockAddStates.waiting_config_name)
async def process_stock_name(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text.strip()
    name = data["suggested_name"] if text in ("تایید", "/confirm", "تایید ✅") else text
    await state.update_data(config_name=name)
    await message.answer("حالا لینک ساب/کانفیگ را ارسال کنید:")
    await state.set_state(StockAddStates.waiting_config_link)


@router.message(StockAddStates.waiting_config_link)
async def process_stock_link(message: Message, state: FSMContext):
    data = await state.get_data()
    async with async_session() as session:
        session.add(StockConfig(
            service_id=data["plan_id"], config_name=data["config_name"],
            config_link=message.text.strip(), source="MANUAL", status="AVAILABLE",
        ))
        await session.commit()
    await message.answer(
        f"✅ کانفیگ `{data['config_name']}` به انبار اضافه شد.",
        parse_mode="Markdown", reply_markup=admin_main_menu_kb(),
    )
    await state.clear()


# ==================== پنل‌ها ====================

@router.callback_query(F.data == "admin_panels")
async def cb_panels(callback: CallbackQuery):
    async with async_session() as session:
        panels = (await session.execute(select(Panel))).scalars().all()
    text = "🖥 پنل‌های ثبت‌شده (برای حذف روی هرکدوم کلیک کن):\n\n"
    if not panels:
        text += "هنوز پنلی ثبت نشده.\n"
    kb_rows = [[InlineKeyboardButton(text=f"#{p.id} {p.name} ({p.panel_type})", callback_data=f"panelinfo_{p.id}")]
               for p in panels]
    kb_rows.append([InlineKeyboardButton(text="➕ افزودن پنل جدید", callback_data="panel_new")])
    kb_rows.append([InlineKeyboardButton(text="🔗 اتصال یک سرویس به پنل", callback_data="panel_assign")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data.startswith("panelinfo_"))
async def cb_panelinfo(callback: CallbackQuery):
    panel_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        panel = await session.get(Panel, panel_id)
        plans_using = await session.scalar(
            select(func.count(ServicePlan.id)).where(ServicePlan.panel_id == panel_id)
        )
    kb_rows = []
    if panel.panel_type == "pasarguard":
        kb_rows.append([InlineKeyboardButton(text="🔢 تنظیم Group IDs", callback_data=f"panelgroups_{panel_id}")])
    elif panel.panel_type == "marzban":
        kb_rows.append([InlineKeyboardButton(text="🔧 تنظیم پروتکل", callback_data=f"panelprotocol_{panel_id}")])
        kb_rows.append([InlineKeyboardButton(text="🏷 تنظیم تگ اینباند (خالی=خودکار)",
                                              callback_data=f"panelinbounds_{panel_id}")])
    kb_rows.append([InlineKeyboardButton(
        text=f"🔗 ادغام ساب این پنل: {'فعال ✅' if panel.submerge_enabled else 'غیرفعال ❌'}",
        callback_data=f"paneltogglesubmerge_{panel_id}",
    )])
    kb_rows.append([InlineKeyboardButton(text="🗑 حذف این پنل", callback_data=f"paneldelete_{panel_id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_panels")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    extra = ""
    if panel.panel_type == "pasarguard":
        extra = f"Group IDs: {panel.group_ids or '1'}\n"
    elif panel.panel_type == "marzban":
        extra = f"پروتکل: {panel.protocol or 'vless'}\nتگ اینباند: {panel.inbound_tags or '(خودکار از پنل گرفته میشه)'}\n"

    await callback.message.edit_text(
        f"🖥 {panel.name}\n\nنوع: {panel.panel_type}\nآدرس: {panel.url}\n"
        f"{extra}"
        f"🔗 ادغام ساب این پنل: {'فعال ✅ (فقط اگه تنظیمات کلی ادغام ساب هم فعال باشه)' if panel.submerge_enabled else 'غیرفعال ❌'}\n"
        f"تعداد پلن‌های وصل‌شده به این پنل: {plans_using}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("paneltogglesubmerge_"))
async def cb_panel_toggle_submerge(callback: CallbackQuery):
    panel_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        panel = await session.get(Panel, panel_id)
        panel.submerge_enabled = not panel.submerge_enabled
        await session.commit()
    await cb_panelinfo(callback)


@router.callback_query(F.data.startswith("panelprotocol_"))
async def cb_panel_protocol_start(callback: CallbackQuery, state: FSMContext):
    panel_id = int(callback.data.split("_")[1])
    await state.update_data(panel_id=panel_id)
    await callback.message.answer(
        "پروتکل اصلی این پنل رو بفرست (یکی از: vless / vmess / trojan / shadowsocks):"
    )
    await state.set_state(PanelMarzbanStates.waiting_protocol)
    await callback.answer()


@router.message(PanelMarzbanStates.waiting_protocol)
async def process_panel_protocol(message: Message, state: FSMContext):
    protocol = message.text.strip().lower()
    if protocol not in ("vless", "vmess", "trojan", "shadowsocks"):
        await message.answer("❌ فقط یکی از این‌ها: vless / vmess / trojan / shadowsocks")
        return
    data = await state.get_data()
    async with async_session() as session:
        panel = await session.get(Panel, data["panel_id"])
        panel.protocol = protocol
        await session.commit()
    await message.answer(f"✅ پروتکل پنل «{panel.name}» به «{protocol}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("panelinbounds_"))
async def cb_panel_inbounds_start(callback: CallbackQuery, state: FSMContext):
    panel_id = int(callback.data.split("_")[1])
    await state.update_data(panel_id=panel_id)
    await callback.message.answer(
        "تگ اینباند(های) پروتکل انتخابی رو بفرست (دقیقاً همون چیزی که توی پنل، ستون tag اینباند نشون میده).\n"
        "اگه چندتاست با کاما جدا کن. اگه خالی بفرستی (فقط یه فاصله یا -)، خودکار از پنل گرفته میشه:"
    )
    await state.set_state(PanelMarzbanStates.waiting_inbound_tags)
    await callback.answer()


@router.message(PanelMarzbanStates.waiting_inbound_tags)
async def process_panel_inbounds(message: Message, state: FSMContext):
    raw = message.text.strip()
    value = None if raw in ("-", "") else raw
    data = await state.get_data()
    async with async_session() as session:
        panel = await session.get(Panel, data["panel_id"])
        panel.inbound_tags = value
        await session.commit()
    shown = value or "(خودکار)"
    await message.answer(f"✅ تگ اینباند پنل «{panel.name}» به «{shown}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("panelgroups_"))
async def cb_panel_groups_start(callback: CallbackQuery, state: FSMContext):
    panel_id = int(callback.data.split("_")[1])
    await state.update_data(panel_id=panel_id)
    await callback.message.answer(
        "شماره‌ی گروه(های) این پنل رو بفرست. اگه چندتاست با کاما جدا کن (مثلا: 1 یا 1,2,3):"
    )
    await state.set_state(PanelGroupsStates.waiting_group_ids)
    await callback.answer()


@router.message(PanelGroupsStates.waiting_group_ids)
async def process_panel_groups(message: Message, state: FSMContext):
    raw = message.text.strip()
    parts = [p.strip() for p in raw.split(",")]
    if not all(p.isdigit() for p in parts):
        await message.answer("❌ فقط عدد بفرست، با کاما جدا (مثلا 1 یا 1,2,3):")
        return
    data = await state.get_data()
    async with async_session() as session:
        panel = await session.get(Panel, data["panel_id"])
        panel.group_ids = raw
        await session.commit()
    await message.answer(f"✅ Group IDs پنل «{panel.name}» به «{raw}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("paneldelete_"))
async def cb_paneldelete(callback: CallbackQuery):
    panel_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        panel = await session.get(Panel, panel_id)
        if panel:
            await session.delete(panel)
            # پلن‌هایی که به این پنل وصل بودن رو آزاد می‌کنیم تا تحویل خودکارشون خطا ندن
            plans = (await session.execute(select(ServicePlan).where(ServicePlan.panel_id == panel_id))).scalars().all()
            for p in plans:
                p.panel_id = None
            await session.commit()
    await callback.message.edit_text(
        "✅ پنل حذف شد. (پلن‌هایی که بهش وصل بودن آزاد شدن - تا پنل جدید وصل نکنی، تحویلشون به‌صورت دستی درخواست میشه.)",
        reply_markup=admin_main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "panel_new")
async def panel_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("اسم دلخواه برای این پنل را وارد کنید (مثلا Prime Panel):")
    await state.set_state(PanelAddStates.waiting_name)
    await callback.answer()


@router.message(PanelAddStates.waiting_name)
async def panel_name_in(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("نوع پنل را وارد کنید: marzban یا pasarguard یا youpanel")
    await state.set_state(PanelAddStates.waiting_type)


@router.message(PanelAddStates.waiting_type)
async def panel_type_in(message: Message, state: FSMContext):
    t = message.text.strip().lower()
    if t not in ("marzban", "pasarguard", "youpanel"):
        await message.answer("❌ فقط یکی از این‌ها رو بفرستید: marzban / pasarguard / youpanel")
        return
    await state.update_data(panel_type=t)
    await message.answer("آدرس پنل را وارد کنید (مثلا https://panel.example.com):")
    await state.set_state(PanelAddStates.waiting_url)


@router.message(PanelAddStates.waiting_url)
async def panel_url_in(message: Message, state: FSMContext):
    from urllib.parse import urlparse, urlunparse
    raw = message.text.strip()
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "https://" + raw
    parsed = urlparse(raw)
    # فقط fragment (بعد از #) که هیچوقت به سرور فرستاده نمیشه رو حذف می‌کنیم،
    # ولی مسیر (path) رو دست نمی‌زنیم چون بعضی پنل‌ها زیر یک ساب‌مسیر مثل /dashboard اجرا میشن
    clean_url = urlunparse(parsed._replace(fragment="", query="")).rstrip("/")

    await state.update_data(url=clean_url)
    await message.answer(
        f"✅ آدرس ذخیره شد: `{clean_url}`\n\n"
        f"⚠️ اگه API این پنل زیر یک مسیر خاص کار می‌کنه (مثلاً باید بعد از ورود به داشبورد وارد بشی)، "
        f"همون مسیر رو هم توی همین آدرس بذار (مثلاً `.../dashboard`). اگه بعداً دیدی وصل نمیشه، "
        f"می‌تونی همین پنل رو دوباره با آدرس درست‌شده اضافه کنی.\n\n"
        f"یوزرنیم ادمین پنل را وارد کنید:",
        parse_mode="Markdown",
    )
    await state.set_state(PanelAddStates.waiting_username)


@router.message(PanelAddStates.waiting_username)
async def panel_username_in(message: Message, state: FSMContext):
    await state.update_data(username=message.text.strip())
    await message.answer("پسورد ادمین پنل را وارد کنید:\n(بعد از ثبت، پیامتون حذف میشه)")
    await state.set_state(PanelAddStates.waiting_password)


@router.message(PanelAddStates.waiting_password)
async def panel_password_in(message: Message, state: FSMContext):
    data = await state.get_data()
    password = message.text.strip()
    async with async_session() as session:
        session.add(Panel(
            name=data["name"], panel_type=data["panel_type"], url=data["url"],
            username=data["username"], password=password,
        ))
        await session.commit()
    try:
        await message.delete()  # حذف پیام حاوی پسورد برای امنیت
    except Exception:
        pass
    await message.answer(f"✅ پنل «{data['name']}» ثبت شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "panel_assign")
async def panel_assign_start(callback: CallbackQuery):
    async with async_session() as session:
        plans = (await session.execute(select(ServicePlan))).scalars().all()
    await callback.message.edit_text(
        "کدام سرویس را می‌خواهید به یک پنل وصل کنید؟",
        reply_markup=admin_plans_list_kb(plans, "assignplan"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("assignplan_"))
async def panel_assign_plan(callback: CallbackQuery):
    plan_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        panels = (await session.execute(select(Panel).where(Panel.active == True))).scalars().all()
    if not panels:
        await callback.message.edit_text("هیچ پنل فعالی ثبت نشده. اول یک پنل اضافه کنید.",
                                          reply_markup=back_to_admin_main_kb())
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=f"{p.name} ({p.panel_type})", callback_data=f"doassign_{plan_id}_{p.id}")]
            for p in panels]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await callback.message.edit_text("کدام پنل؟", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("doassign_"))
async def panel_assign_do(callback: CallbackQuery):
    _, plan_id, panel_id = callback.data.split("_")
    async with async_session() as session:
        plan = await session.get(ServicePlan, int(plan_id))
        plan.panel_id = int(panel_id)
        await session.commit()
        plan_name = plan.name
    await callback.message.edit_text(f"✅ سرویس «{plan_name}» به پنل انتخابی وصل شد.",
                                      reply_markup=back_to_admin_main_kb())
    await callback.answer()


# ==================== تست رایگان ====================

@router.callback_query(F.data == "admin_trials")
async def cb_admin_trials(callback: CallbackQuery):
    async with async_session() as session:
        trials = (await session.execute(select(TrialPlan).order_by(TrialPlan.sort_order))).scalars().all()
        used_count = await session.scalar(select(func.count(User.id)).where(User.used_free_trial == True))

    rows = []
    for t in trials:
        status = "✅" if t.active else "❌"
        mb = int(round((t.volume_gb or 0) * 1024))
        rows.append([InlineKeyboardButton(
            text=f"{status} {t.name} ({mb}mb/{t.duration_days}روز)",
            callback_data=f"trialinfo_{t.id}",
        )])
    rows.append([InlineKeyboardButton(text="➕ افزودن تست رایگان جدید", callback_data="trial_new")])
    rows.append([InlineKeyboardButton(
        text=f"♻️ ریست تست رایگان برای همه ({used_count} نفر استفاده کردن)",
        callback_data="trial_reset_confirm",
    )])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await callback.message.edit_text(
        "🎁 مدیریت تست رایگان\n\n"
        "هر کاربر (تا وقتی ریست نکنی) فقط یه‌بار می‌تونه از تست رایگان استفاده کنه. "
        "اگه چند تست فعال باشه، کاربر یکی رو از لیست انتخاب می‌کنه.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "trial_reset_confirm")
async def cb_trial_reset_confirm(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ بله، ریست کن", callback_data="trial_reset_do"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="admin_trials"),
    ]])
    await callback.message.edit_text(
        "⚠️ مطمئنی؟ با این کار همه‌ی کاربرایی که قبلاً از تست رایگان استفاده کردن، دوباره "
        "می‌تونن یه تست رایگان دیگه بگیرن.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "trial_reset_do")
async def cb_trial_reset_do(callback: CallbackQuery):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.used_free_trial == True))
        users = result.scalars().all()
        for u in users:
            u.used_free_trial = False
        await session.commit()
    await callback.answer(f"✅ برای {len(users)} کاربر ریست شد.", show_alert=True)
    await cb_admin_trials(callback)


@router.callback_query(F.data == "trial_new")
async def cb_trial_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("نام این تست رایگان رو وارد کن (مثلا «تست ۱۰۰ مگ لیدسو»):")
    await state.set_state(TrialAddStates.waiting_name)
    await callback.answer()


@router.message(TrialAddStates.waiting_name)
async def trial_new_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("حجم تست به مگابایت رو وارد کن (مثلا برای ۱۰۰ مگ، عدد 100 رو بفرست):")
    await state.set_state(TrialAddStates.waiting_volume_mb)


@router.message(TrialAddStates.waiting_volume_mb)
async def trial_new_volume(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ فقط عدد مثبت (مگابایت) وارد کن:")
        return
    await state.update_data(volume_mb=int(text))
    await message.answer("مدت زمان تست به روز رو وارد کن (مثلا برای یک روز، عدد 1 رو بفرست):")
    await state.set_state(TrialAddStates.waiting_duration_days)


@router.message(TrialAddStates.waiting_duration_days)
async def trial_new_duration(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ فقط عدد مثبت (روز) وارد کن:")
        return
    await state.update_data(duration_days=int(text))

    async with async_session() as session:
        panels = (await session.execute(select(Panel).where(Panel.active == True))).scalars().all()
    if not panels:
        await message.answer("⚠️ هیچ پنل فعالی ثبت نشده. اول از بخش «پنل‌ها» یه پنل اضافه کن.",
                              reply_markup=admin_main_menu_kb())
        await state.clear()
        return

    rows = [[InlineKeyboardButton(text=f"{p.name} ({p.panel_type})", callback_data=f"trialpanel_{p.id}")]
            for p in panels]
    await message.answer("این تست رایگان روی کدوم پنل ساخته بشه؟", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.set_state(TrialAddStates.waiting_panel)


@router.callback_query(TrialAddStates.waiting_panel, F.data.startswith("trialpanel_"))
async def trial_new_panel(callback: CallbackQuery, state: FSMContext):
    panel_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    volume_gb = round(data["volume_mb"] / 1024, 4)

    async with async_session() as session:
        trial = TrialPlan(
            name=data["name"], prefix="LidsoTest", volume_gb=volume_gb,
            duration_days=data["duration_days"], panel_id=panel_id, active=True,
        )
        session.add(trial)
        await session.commit()

    await callback.message.edit_text(
        f"✅ تست رایگان «{data['name']}» ساخته شد ({data['volume_mb']}مگابایت / {data['duration_days']}روز).",
        reply_markup=back_to_admin_main_kb(),
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("trialinfo_"))
async def cb_trialinfo(callback: CallbackQuery):
    trial_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        trial = await session.get(TrialPlan, trial_id)
        panel = await session.get(Panel, trial.panel_id) if trial.panel_id else None
        used_count = await session.scalar(
            select(func.count(ServiceOrder.id)).where(ServiceOrder.plan_id == trial_id, ServiceOrder.is_trial == True)
        )

    mb = int(round((trial.volume_gb or 0) * 1024))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش نام", callback_data=f"trialname_{trial_id}")],
        [InlineKeyboardButton(text="✏️ ویرایش حجم (مگابایت)", callback_data=f"trialvol_{trial_id}")],
        [InlineKeyboardButton(text="✏️ ویرایش مدت زمان (روز)", callback_data=f"trialdur_{trial_id}")],
        [InlineKeyboardButton(text="🔁 تغییر پنل", callback_data=f"trialchangepanel_{trial_id}")],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کردن" if trial.active else "✅ فعال کردن",
            callback_data=f"trialtoggle_{trial_id}",
        )],
        [InlineKeyboardButton(text="🗑 حذف این تست رایگان", callback_data=f"trialdelete_{trial_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_trials")],
    ])
    await callback.message.edit_text(
        f"🎁 {trial.name}\n\nحجم: {mb} مگابایت\nمدت زمان: {trial.duration_days} روز\n"
        f"پنل: {panel.name if panel else '❌ وصل نیست'}\n"
        f"وضعیت: {'فعال ✅' if trial.active else 'غیرفعال ❌'}\n"
        f"تعداد استفاده تا الان: {used_count}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("trialtoggle_"))
async def cb_trialtoggle(callback: CallbackQuery):
    trial_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        trial = await session.get(TrialPlan, trial_id)
        trial.active = not trial.active
        await session.commit()
    await cb_trialinfo(callback)


@router.callback_query(F.data.startswith("trialdelete_"))
async def cb_trialdelete(callback: CallbackQuery):
    trial_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        trial = await session.get(TrialPlan, trial_id)
        if trial:
            await session.delete(trial)
            await session.commit()
    await callback.answer("✅ حذف شد.")
    await cb_admin_trials(callback)


@router.callback_query(F.data.startswith("trialname_"))
async def cb_trialname_start(callback: CallbackQuery, state: FSMContext):
    trial_id = int(callback.data.split("_")[1])
    await state.update_data(trial_id=trial_id)
    await callback.message.answer("نام جدید رو وارد کن:")
    await state.set_state(TrialEditStates.waiting_new_name)
    await callback.answer()


@router.message(TrialEditStates.waiting_new_name)
async def process_trial_new_name(message: Message, state: FSMContext):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("❌ نام نمی‌تواند خالی باشد. دوباره وارد کنید:")
        return
    data = await state.get_data()
    async with async_session() as session:
        trial = await session.get(TrialPlan, data["trial_id"])
        trial.name = new_name
        await session.commit()
    await message.answer(f"✅ نام به «{new_name}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("trialvol_"))
async def cb_trialvol_start(callback: CallbackQuery, state: FSMContext):
    trial_id = int(callback.data.split("_")[1])
    await state.update_data(trial_id=trial_id)
    await callback.message.answer("حجم جدید رو به مگابایت وارد کن (مثلا 100):")
    await state.set_state(TrialEditStates.waiting_new_volume_mb)
    await callback.answer()


@router.message(TrialEditStates.waiting_new_volume_mb)
async def process_trial_new_volume(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ فقط عدد مثبت (مگابایت) وارد کن:")
        return
    data = await state.get_data()
    async with async_session() as session:
        trial = await session.get(TrialPlan, data["trial_id"])
        trial.volume_gb = round(int(text) / 1024, 4)
        await session.commit()
    await message.answer(f"✅ حجم به {text} مگابایت تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("trialdur_"))
async def cb_trialdur_start(callback: CallbackQuery, state: FSMContext):
    trial_id = int(callback.data.split("_")[1])
    await state.update_data(trial_id=trial_id)
    await callback.message.answer("مدت زمان جدید رو به روز وارد کن (مثلا 1):")
    await state.set_state(TrialEditStates.waiting_new_duration_days)
    await callback.answer()


@router.message(TrialEditStates.waiting_new_duration_days)
async def process_trial_new_duration(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ فقط عدد مثبت (روز) وارد کن:")
        return
    data = await state.get_data()
    async with async_session() as session:
        trial = await session.get(TrialPlan, data["trial_id"])
        trial.duration_days = int(text)
        await session.commit()
    await message.answer(f"✅ مدت زمان به {text} روز تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("trialchangepanel_"))
async def cb_trial_change_panel(callback: CallbackQuery, state: FSMContext):
    trial_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        panels = (await session.execute(select(Panel).where(Panel.active == True))).scalars().all()
    if not panels:
        await callback.answer("هیچ پنل فعالی ثبت نشده.", show_alert=True)
        return
    rows = [[InlineKeyboardButton(text=f"{p.name} ({p.panel_type})", callback_data=f"trialdopanel_{trial_id}_{p.id}")]
            for p in panels]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"trialinfo_{trial_id}")])
    await callback.message.edit_text("پنل جدید رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("trialdopanel_"))
async def cb_trial_do_change_panel(callback: CallbackQuery):
    _, trial_id, panel_id = callback.data.split("_")
    async with async_session() as session:
        trial = await session.get(TrialPlan, int(trial_id))
        trial.panel_id = int(panel_id)
        await session.commit()
    await callback.answer("✅ پنل تغییر کرد.")
    await cb_trialinfo(callback)


# ==================== ویرایش متن‌ها (شامل تمام دکمه‌ها) ====================

MAINMENU_KEYS = {  # کلیدهایی که MenuButton دارن (رنگ/آیکونشون باید روی همون جدول نوشته بشه، نه BotContent)
    "btn_buy", "btn_tariffs", "btn_my_services", "btn_wallet", "btn_profile", "btn_invite",
    "btn_guide", "btn_support", "btn_free_trial",
}

BUTTON_KEYS = {  # این کلیدها فقط متن دکمه‌ان - تلگرام روی این‌ها فرمت/ایموجی پرمیوم پشتیبانی نمی‌کنه
    "btn_buy", "btn_tariffs", "btn_my_services", "btn_wallet", "btn_profile", "btn_invite",
    "btn_guide", "btn_support", "btn_back_main", "btn_back_services", "btn_back", "btn_renew",
    "btn_duration_1m", "btn_send_phone", "btn_wallet_card", "btn_wallet_gateway",
    "btn_wallet_crypto", "btn_wallet_discount", "btn_free_trial",
}

SETTINGS_CATEGORIES = {
    "mainmenu": [
        ("btn_buy", "🛍 خرید سرویس"), ("btn_tariffs", "🏷 تعرفه‌ها"),
        ("btn_my_services", "📦 سرویس‌های من"), ("btn_wallet", "💳 کیف پول"),
        ("btn_profile", "👤 پروفایل من"), ("btn_invite", "👥 دعوت دوستان"),
        ("btn_guide", "📚 آموزش اتصال"), ("btn_support", "📞 پشتیبانی"),
        ("btn_free_trial", "🎁 تست رایگان"),
    ],
    "nav": [
        ("btn_back_main", "متن دکمه: بازگشت به منوی اصلی"),
        ("btn_back_services", "متن دکمه: بازگشت به منوی سرویس‌ها"),
        ("btn_back", "متن دکمه: بازگشت (عمومی، کیف پول)"),
        ("btn_renew", "متن دکمه: تمدید اشتراک"),
        ("btn_duration_1m", "متن دکمه: یکماهه"),
        ("btn_send_phone", "متن دکمه: ارسال شماره تلفن"),
    ],
    "wallet": [
        ("btn_wallet_card", "متن دکمه: کارت به کارت"),
        ("btn_wallet_gateway", "متن دکمه: پرداخت با درگاه"),
        ("btn_wallet_crypto", "متن دکمه: پرداخت ارزی"),
        ("btn_wallet_discount", "متن دکمه: کد تخفیف"),
    ],
    "content": [
        ("tariffs", "📝 متن تعرفه‌ها (ایموجی پرمیوم/فرمت پشتیبانی میشه)"),
        ("guide", "📝 متن آموزش اتصال (ایموجی پرمیوم/فرمت پشتیبانی میشه)"),
        ("welcome", "👋 متن خوش‌آمدگویی (ایموجی پرمیوم/فرمت پشتیبانی میشه)"),
        ("order_processing_text", "⏳ متن «سفارش در حال آماده‌سازیه»"),
        ("trial_processing_text", "⏳ متن «تست رایگان در حال ساخته‌شدنه»"),
        ("support", "📞 متن کامل پشتیبانی (فرمت/ایموجی پرمیوم)"),
        ("support_id", "🆔 آیدی پشتیبانی"), ("card_number", "💳 شماره کارت"),
        ("card_holder", "👤 نام صاحب کارت"),
    ],
    "topup_limits": [
        ("min_topup_card", "💰 حداقل شارژ - کارت به کارت"),
        ("max_topup_card", "💰 حداکثر شارژ - کارت به کارت (۰ = بدون سقف)"),
        ("min_topup_gateway", "💰 حداقل شارژ - درگاه پرداخت"),
        ("max_topup_gateway", "💰 حداکثر شارژ - درگاه پرداخت (۰ = بدون سقف)"),
        ("min_topup_crypto", "💰 حداقل شارژ - پرداخت ارزی"),
        ("max_topup_crypto", "💰 حداکثر شارژ - پرداخت ارزی (۰ = بدون سقف)"),
    ],
    "channel": [
        ("required_channel", "🔒 آیدی کانال عضویت اجباری (خالی = غیرفعال)"),
    ],
}

SETTINGS_CATEGORY_TITLES = {
    "mainmenu": "🔘 دکمه‌های منوی اصلی",
    "nav": "🔙 دکمه‌های ناوبری",
    "wallet": "💳 دکمه‌های کیف پول",
    "content": "📝 متن‌های محتوا",
    "topup_limits": "💰 حداقل/حداکثر شارژ کیف پول",
    "channel": "🔒 عضویت اجباری کانال",
}

# کلیدهایی که باید عدد صحیح باشن (نه هر متنی)
NUMERIC_KEYS = {
    "min_topup_card", "max_topup_card", "min_topup_gateway", "max_topup_gateway",
    "min_topup_crypto", "max_topup_crypto", "min_topup_amount",
}


@router.callback_query(F.data == "admin_settings")
async def cb_settings(callback: CallbackQuery):
    method_keys = ["require_phone_for_card", "payment_method_card_enabled",
                   "payment_method_gateway_enabled", "payment_method_crypto_enabled"]
    async with async_session() as session:
        rows_db = (await session.execute(
            select(BotContent.key, BotContent.value).where(BotContent.key.in_(method_keys))
        )).all()
    flags = {k: v for k, v in rows_db}
    require_phone = flags.get("require_phone_for_card", "1") == "1"
    card_on = flags.get("payment_method_card_enabled", "1") != "0"
    gateway_on = flags.get("payment_method_gateway_enabled", "1") != "0"
    crypto_on = flags.get("payment_method_crypto_enabled", "1") != "0"

    rows = [[InlineKeyboardButton(text=title, callback_data=f"settingscat_{key}")]
            for key, title in SETTINGS_CATEGORY_TITLES.items()]
    rows.append([InlineKeyboardButton(
        text=f"📱 اجبار شماره تلفن (کارت به کارت): {'فعال ✅' if require_phone else 'غیرفعال ❌'}",
        callback_data="toggle_require_phone",
    )])
    rows.append([InlineKeyboardButton(
        text=f"💳 کارت به کارت: {'فعال ✅' if card_on else 'غیرفعال ❌'}",
        callback_data="toggle_pm_card",
    )])
    rows.append([InlineKeyboardButton(
        text=f"🌐 درگاه پرداخت: {'فعال ✅' if gateway_on else 'غیرفعال ❌'}",
        callback_data="toggle_pm_gateway",
    )])
    rows.append([InlineKeyboardButton(
        text=f"🪙 پرداخت ارزی: {'فعال ✅' if crypto_on else 'غیرفعال ❌'}",
        callback_data="toggle_pm_crypto",
    )])
    rows.append([InlineKeyboardButton(text="♻️ بازنشانی دکمه‌ها به پیش‌فرض", callback_data="reset_ui_texts")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await callback.message.edit_text(
        "⚙️ هر متن یا دکمه‌ای که توی ربات هست از همینجا قابل ویرایشه.\n\n"
        "⚠️ توجه: تلگرام روی متن دکمه‌های کیبورد، ایموجی پرمیوم یا فرمت خاصی پشتیبانی نمی‌کنه "
        "(فقط متن ساده). ایموجی پرمیوم فقط توی «متن‌های محتوا» (تعرفه‌ها/آموزش/خوش‌آمدگویی) کار می‌کنه.\n\n"
        "⚠️ متن دکمه‌ها نباید با / شروع بشه (با دستورات ربات قاطی میشه).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "toggle_require_phone")
async def cb_toggle_require_phone(callback: CallbackQuery):
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == "require_phone_for_card"))
        current = (row.value if row else "1") == "1"
        new_val = "0" if current else "1"
        if row:
            row.value = new_val
        else:
            session.add(BotContent(key="require_phone_for_card", value=new_val))
        await session.commit()
    await cb_settings(callback)


@router.callback_query(F.data.startswith("toggle_pm_"))
async def cb_toggle_payment_method(callback: CallbackQuery):
    method = callback.data.split("_")[-1]  # card / gateway / crypto
    all_keys = ["payment_method_card_enabled", "payment_method_gateway_enabled", "payment_method_crypto_enabled"]
    key = f"payment_method_{method}_enabled"

    async with async_session() as session:
        rows_db = (await session.execute(
            select(BotContent.key, BotContent.value).where(BotContent.key.in_(all_keys))
        )).all()
        flags = {k: v for k, v in rows_db}
        current = flags.get(key, "1") != "0"

        if current:
            # جلوگیری از غیرفعال کردن هر سه روش پرداخت با هم - کاربر باید حداقل یه راه برای شارژ داشته باشه
            enabled_after = [k for k in all_keys if k != key and flags.get(k, "1") != "0"]
            if not enabled_after:
                await callback.answer("❌ نمی‌تونی همه‌ی روش‌های پرداخت رو غیرفعال کنی! حداقل یکی باید فعال بمونه.",
                                       show_alert=True)
                return

        new_val = "0" if current else "1"
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if row:
            row.value = new_val
        else:
            session.add(BotContent(key=key, value=new_val))
        await session.commit()
    await cb_settings(callback)


@router.callback_query(F.data == "reset_ui_texts")
async def cb_reset_ui_texts(callback: CallbackQuery):
    from ui_texts import reset_all_ui_texts
    await reset_all_ui_texts()
    await callback.message.edit_text("✅ همه‌ی دکمه‌ها به مقدار پیش‌فرض برگشتن.", reply_markup=admin_main_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("settingscat_"))
async def cb_settings_category(callback: CallbackQuery):
    cat = callback.data.split("_", 1)[1]
    keys = SETTINGS_CATEGORIES.get(cat, [])
    rows = [[InlineKeyboardButton(text=label, callback_data=f"editcontent_{key}")] for key, label in keys]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_settings")])
    await callback.message.edit_text(
        f"{SETTINGS_CATEGORY_TITLES.get(cat, '')} — کدام مورد را ویرایش می‌کنید؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editcontent_"))
async def cb_editcontent(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split("_", 1)[1]
    await state.update_data(content_key=key)

    if key in MAINMENU_KEYS:
        # این کلیدها MenuButton دارن - رنگ/آیکونشون از همونجا خونده و نوشته میشه (نه BotContent)
        from ui_texts import get_text
        async with async_session() as session:
            menu_row = await session.scalar(select(MenuButton).where(MenuButton.key == key))
        current = await get_text(key)  # متن پیش‌فرض/فعلی همچنان از سیستم مرکزی متن‌ها میاد
        icon_status = "دارد ✅" if (menu_row and menu_row.icon_custom_emoji_id) else "ندارد"
        style_fa = {"primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز"}.get(
            menu_row.style if menu_row else None, "پیش‌فرض"
        )
        hint = (
            f"\n\n🖼 آیکون ایموجی پرمیوم: {icon_status}\n🎨 رنگ فعلی: {style_fa}\n\n"
            f"⚠️ متن دکمه هنوز نباید با / شروع بشه. برای آیکون/رنگ از دکمه‌های پایین استفاده کن؛ "
            f"برای عوض کردن خودِ متن، پیام جدید بفرست."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼 تنظیم آیکون ایموجی پرمیوم", callback_data=f"mmicon_{key}")],
            [
                InlineKeyboardButton(text="🔵 آبی", callback_data=f"mmstyle_{key}_primary"),
                InlineKeyboardButton(text="🟢 سبز", callback_data=f"mmstyle_{key}_success"),
                InlineKeyboardButton(text="🔴 قرمز", callback_data=f"mmstyle_{key}_danger"),
                InlineKeyboardButton(text="⚪️ پیش‌فرض", callback_data=f"mmstyle_{key}_none"),
            ],
        ])
        await callback.message.answer(
            f"مقدار فعلی:\n\n{current or '(خالی - از پیش‌فرض استفاده میشه)'}\n\n"
            f"متن جدید را ارسال کنید:{hint}",
            reply_markup=kb,
        )
        await state.set_state(SettingsEditStates.waiting_new_value)
        await callback.answer()
        return

    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        current = row.value if row else None
        default_value = (row.default_value if row and row.default_value else None)
        use_default = bool(row.use_default) if row and row.use_default is not None else True
        default_position = (row.default_position if row else "before") or "before"

    if key in BUTTON_KEYS:
        icon_status = "دارد ✅" if (row and row.icon_custom_emoji_id) else "ندارد"
        style_fa = {"primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز"}.get(
            row.style if row else None, "پیش‌فرض"
        )
        hint = (
            f"\n\n🖼 آیکون ایموجی پرمیوم: {icon_status}\n🎨 رنگ فعلی: {style_fa}\n\n"
            f"⚠️ متن دکمه هنوز نباید با / شروع بشه. برای آیکون/رنگ از دکمه‌های پایین استفاده کن؛ "
            f"برای عوض کردن خودِ متن، پیام جدید بفرست."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼 تنظیم آیکون ایموجی پرمیوم", callback_data=f"btnicon_{key}")],
            [
                InlineKeyboardButton(text="🔵 آبی", callback_data=f"btnstyle_{key}_primary"),
                InlineKeyboardButton(text="🟢 سبز", callback_data=f"btnstyle_{key}_success"),
                InlineKeyboardButton(text="🔴 قرمز", callback_data=f"btnstyle_{key}_danger"),
                InlineKeyboardButton(text="⚪️ پیش‌فرض", callback_data=f"btnstyle_{key}_none"),
            ],
        ])
    else:
        hint = ("\n\n✅ فرمت‌بندی تلگرام (بولد/ایتالیک/لینک/ایموجی پرمیوم) ذخیره می‌شود.\n"
                "🔧 از دکمه‌های زیر می‌توانید استفاده از پیش‌فرض و جای آن را کنترل کنید.")
        rows = [
            [InlineKeyboardButton(text=f"📌 متن پیش‌فرض: {'فعال ✅' if use_default else 'غیرفعال ❌'}", callback_data=f"toggle_default_{key}")],
            [
                InlineKeyboardButton(text=f"⬆️ پیش‌فرض قبل {'✅' if default_position == 'before' else ''}", callback_data=f"defaultpos_{key}_before"),
                InlineKeyboardButton(text=f"⬇️ پیش‌فرض بعد {'✅' if default_position == 'after' else ''}", callback_data=f"defaultpos_{key}_after"),
            ],
            [InlineKeyboardButton(text="📝 ویرایش متن پیش‌فرض", callback_data=f"editdefault_{key}")],
            [InlineKeyboardButton(text="♻️ حذف متن سفارشی / برگشت به پیش‌فرض", callback_data=f"clearcustom_{key}")],
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await callback.message.answer(
        f"مقدار فعلی سفارشی:\n\n{current or '(ندارد)'}\n\n"
        f"متن پیش‌فرض فعلی:\n\n{default_value or '(ندارد)'}{hint}",
        reply_markup=kb,
    )
    await state.set_state(SettingsEditStates.waiting_new_value)
    await callback.answer()


@router.callback_query(F.data.startswith("mmicon_"))
async def cb_mainmenu_icon_start(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split("_", 1)[1]
    await state.update_data(menu_key_icon=key)
    await callback.message.answer("ایموجی پرمیومی که می‌خوای کنار این دکمه نشون داده بشه رو مستقیم از پنل ایموجی تلگرام بفرست:")
    await state.set_state(MenuEditStates.waiting_icon_emoji)
    await callback.answer()


@router.callback_query(F.data.startswith("mmstyle_"))
async def cb_mainmenu_style(callback: CallbackQuery):
    rest = callback.data[len("mmstyle_"):]  # "{key}_{style}"
    key, style = rest.rsplit("_", 1)
    style_val = None if style == "none" else style
    async with async_session() as session:
        menu_row = await session.scalar(select(MenuButton).where(MenuButton.key == key))
        if menu_row:
            menu_row.style = style_val
            await session.commit()
    await callback.answer("✅ رنگ ذخیره شد. برای دیدنش دوباره وارد منو شو.")


@router.callback_query(F.data.startswith("btnicon_"))
async def cb_btn_icon_start(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split("_", 1)[1]
    await state.update_data(icon_key=key)
    await callback.message.answer("ایموجی پرمیومی که می‌خوای کنار این دکمه نشون داده بشه رو مستقیم از پنل ایموجی تلگرام بفرست:")
    await state.set_state(MenuEditStates.waiting_icon_emoji)
    await callback.answer()


@router.message(MenuEditStates.waiting_icon_emoji)
async def process_btn_icon(message: Message, state: FSMContext):
    custom_emoji = next((e for e in (message.entities or []) if e.type == "custom_emoji"), None)
    if not custom_emoji:
        await message.answer(
            "⚠️ توی این پیام هیچ ایموجی پرمیوم واقعی شناسایی نشد. مستقیم از پنل ایموجی خودِ تلگرام "
            "(نه کپی از جای دیگه) یکی رو انتخاب کن و دوباره بفرست:"
        )
        return
    data = await state.get_data()
    async with async_session() as session:
        if "icon_key" in data:
            row = await session.scalar(select(BotContent).where(BotContent.key == data["icon_key"]))
            if row:
                row.icon_custom_emoji_id = custom_emoji.custom_emoji_id
            else:
                session.add(BotContent(key=data["icon_key"], value=data["icon_key"],
                                        icon_custom_emoji_id=custom_emoji.custom_emoji_id))
            await session.commit()
            from ui_texts import invalidate_cache
            invalidate_cache(data["icon_key"])
        elif "menu_item_id" in data:
            btn = await session.get(MenuButton, data["menu_item_id"])
            btn.icon_custom_emoji_id = custom_emoji.custom_emoji_id
            await session.commit()
        elif "category_icon_id" in data:
            category = await session.get(Category, data["category_icon_id"])
            category.icon_custom_emoji_id = custom_emoji.custom_emoji_id
            await session.commit()
        elif "plan_icon_id" in data:
            plan = await session.get(ServicePlan, data["plan_icon_id"])
            plan.icon_custom_emoji_id = custom_emoji.custom_emoji_id
            await session.commit()
        elif "menu_key_icon" in data:
            key = data["menu_key_icon"]
            menu_row = await session.scalar(select(MenuButton).where(MenuButton.key == key))
            if menu_row:
                menu_row.icon_custom_emoji_id = custom_emoji.custom_emoji_id
                await session.commit()
    await message.answer("✅ آیکون این دکمه ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data.startswith("catticon_"))
async def cb_cat_icon_start(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(category_icon_id=cat_id)
    await callback.message.answer("ایموجی پرمیومی که می‌خوای کنار این دسته‌بندی نشون داده بشه رو مستقیم از پنل ایموجی تلگرام بفرست:")
    await state.set_state(MenuEditStates.waiting_icon_emoji)
    await callback.answer()


@router.callback_query(F.data.startswith("catstyle_"))
async def cb_cat_style(callback: CallbackQuery):
    rest = callback.data[len("catstyle_"):]  # "{cat_id}_{style}"
    cat_id_str, style = rest.rsplit("_", 1)
    style_val = None if style == "none" else style
    async with async_session() as session:
        category = await session.get(Category, int(cat_id_str))
        category.style = style_val
        await session.commit()
    await callback.answer("✅ رنگ ذخیره شد. برای دیدنش دوباره وارد منو شو.")


@router.callback_query(F.data.startswith("planicon_"))
async def cb_plan_icon_start(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split("_")[1])
    await state.update_data(plan_icon_id=plan_id)
    await callback.message.answer("ایموجی پرمیومی که می‌خوای کنار این پلن نشون داده بشه رو مستقیم از پنل ایموجی تلگرام بفرست:")
    await state.set_state(MenuEditStates.waiting_icon_emoji)
    await callback.answer()


@router.callback_query(F.data.startswith("planstyle_"))
async def cb_plan_style(callback: CallbackQuery):
    rest = callback.data[len("planstyle_"):]  # "{plan_id}_{style}"
    plan_id_str, style = rest.rsplit("_", 1)
    style_val = None if style == "none" else style
    async with async_session() as session:
        plan = await session.get(ServicePlan, int(plan_id_str))
        plan.style = style_val
        await session.commit()
    await callback.answer("✅ رنگ ذخیره شد. برای دیدنش دوباره وارد منو شو.")


@router.callback_query(F.data.startswith("btnstyle_"))
async def cb_btn_style(callback: CallbackQuery):
    rest = callback.data[len("btnstyle_"):]  # "{key}_{style}"
    key, style = rest.rsplit("_", 1)
    style_val = None if style == "none" else style
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if row:
            row.style = style_val
        else:
            session.add(BotContent(key=key, value=key, style=style_val))
        await session.commit()
    from ui_texts import invalidate_cache
    invalidate_cache(key)
    await callback.answer("✅ رنگ ذخیره شد. برای دیدنش دوباره وارد منو شو.")


@router.callback_query(F.data.startswith("toggle_default_"))
async def cb_toggle_default(callback: CallbackQuery):
    key = callback.data[len("toggle_default_"):]
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if not row:
            await callback.answer("❌ این متن هنوز ذخیره نشده.", show_alert=True)
            return
        row.use_default = not bool(row.use_default)
        await session.commit()
    await callback.answer("✅ وضعیت متن پیش‌فرض تغییر کرد.")


@router.callback_query(F.data.startswith("defaultpos_"))
async def cb_default_position(callback: CallbackQuery):
    rest = callback.data[len("defaultpos_"):]
    key, position = rest.rsplit("_", 1)
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if not row:
            await callback.answer("❌ این متن هنوز ذخیره نشده.", show_alert=True)
            return
        row.default_position = position
        await session.commit()
    await callback.answer("✅ جای متن پیش‌فرض ذخیره شد.")


@router.callback_query(F.data.startswith("clearcustom_"))
async def cb_clear_custom(callback: CallbackQuery):
    key = callback.data[len("clearcustom_"):]
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if row:
            row.value = None
            row.entities = None
            row.use_default = True
            await session.commit()
    from ui_texts import invalidate_cache
    invalidate_cache(key)
    await callback.answer("✅ متن سفارشی حذف شد و پیش‌فرض فعال شد.")


@router.callback_query(F.data.startswith("editdefault_"))
async def cb_edit_default(callback: CallbackQuery, state: FSMContext):
    key = callback.data[len("editdefault_"):]
    await state.update_data(default_content_key=key)
    await callback.message.answer(
        "📝 متن پیش‌فرض جدید را ارسال کن.\n\n"
        "فرمت‌بندی، بولد، لینک و ایموجی پرمیوم دقیقاً از همین پیام ذخیره می‌شود."
    )
    await state.set_state(SettingsEditStates.waiting_default_value)
    await callback.answer()


@router.message(SettingsEditStates.waiting_default_value)
async def process_default_content(message: Message, state: FSMContext):
    from ui_texts import invalidate_cache, serialize_entities
    data = await state.get_data()
    key = data["default_content_key"]
    new_text = message.text or ""
    async with async_session() as session:
        row = await session.scalar(select(BotContent).where(BotContent.key == key))
        if not row:
            row = BotContent(key=key, value=None, default_value=new_text, use_default=True, default_position="before")
            session.add(row)
        else:
            row.default_value = new_text
            row.use_default = True
        row.default_entities = serialize_entities(message.entities)
        await session.commit()
    invalidate_cache(key)
    await message.answer("✅ متن پیش‌فرض با فرمت‌بندی و ایموجی‌هایش ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.message(SettingsEditStates.waiting_new_value)
async def process_new_content(message: Message, state: FSMContext):
    from ui_texts import invalidate_cache, serialize_entities
    data = await state.get_data()
    key = data["content_key"]
    new_text = message.text or ""

    if key in BUTTON_KEYS and new_text.strip().startswith("/"):
        await message.answer(
            "❌ متن دکمه نمی‌تونه با / شروع بشه چون با دستورات ربات (مثل /start) قاطی میشه.\n"
            "یه متن دیگه بفرست:"
        )
        return

    if key in NUMERIC_KEYS:
        clean_num = new_text.strip().replace(",", "")
        if not clean_num.isdigit():
            await message.answer("❌ این مقدار باید فقط عدد باشه (بدون حروف یا نماد). دوباره وارد کنید:")
            return
        new_text = clean_num

    entities_json = None if key in BUTTON_KEYS else serialize_entities(message.entities)

    async with async_session() as session:
        content = await session.scalar(select(BotContent).where(BotContent.key == key))
        if content:
            content.value = new_text
            content.entities = entities_json
        else:
            session.add(BotContent(key=key, value=new_text, entities=entities_json))
        await session.commit()
    invalidate_cache(key)  # تا همین لحظه اثرش بیفته، نیازی به ریستارت ربات نیست

    # 🔍 تشخیص دقیق: آیا تلگرام واقعاً چیز خاصی (ایموجی پرمیوم/بولد/...) توی پیامت پیدا کرد؟
    custom_emoji_count = sum(1 for e in (message.entities or []) if e.type == "custom_emoji")
    other_entity_count = sum(1 for e in (message.entities or []) if e.type != "custom_emoji")

    if key in BUTTON_KEYS:
        diag = ""
    elif custom_emoji_count:
        diag = f"\n\n🔍 تشخیص: {custom_emoji_count} ایموجی پرمیوم واقعی توی پیامت شناسایی و ذخیره شد ✅"
    elif other_entity_count:
        diag = f"\n\n🔍 تشخیص: {other_entity_count} مورد فرمت‌بندی (بولد/ایتالیک/...) شناسایی شد، ولی ایموجی پرمیوم نه."
    else:
        diag = (
            "\n\n⚠️ تشخیص: تلگرام هیچ ایموجی پرمیوم یا فرمت خاصی توی این پیام شناسایی نکرد "
            "(به‌عنوان متن ساده ذخیره شد). اگه فکر می‌کردی ایموجی پرمیوم فرستادی، احتمالاً موقع "
            "کپی/پیست یا تایپ، فرمتش از بین رفته - مستقیم از پنل ایموجی خودِ تلگرام (نه کپی از "
            "جای دیگه) انتخابش کن و دوباره امتحان کن."
        )

    await message.answer(f"✅ ذخیره شد و از همین الان فعاله.{diag}", reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== پیام همگانی ====================

@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📢 متن پیام همگانی را ارسال کنید:")
    await state.set_state(BroadcastStates.waiting_message)
    await callback.answer()


@router.message(BroadcastStates.waiting_message)
async def broadcast_receive(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await message.answer(f"پیش‌نمایش:\n\n{message.text}\n\nارسال شود؟", reply_markup=confirm_broadcast_kb())


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data.get("text", "")
    async with async_session() as session:
        user_ids = (await session.execute(select(User.user_id))).scalars().all()
    sent = 0
    for uid in user_ids:
        try:
            await callback.bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
    await callback.message.edit_text(f"✅ پیام برای {sent} کاربر ارسال شد.")
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ لغو شد.")
    await callback.answer()


# ==================== تایید/رد تراکنش‌های کیف پول ====================

@router.callback_query(F.data.startswith("tx_approve_"))
async def cb_tx_approve(callback: CallbackQuery):
    tx_id = int(callback.data.split("_")[-1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "PENDING":
            await callback.answer("این تراکنش قبلاً پردازش شده.", show_alert=True)
            return
        user = await session.scalar(select(User).where(User.user_id == tx.user_id))
        user.balance += tx.amount
        tx.status = "SUCCESS"
        tx.handled_by = callback.from_user.id
        await session.commit()
        amount, target_user = tx.amount, tx.user_id

    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption=(callback.message.caption or "") + "\n\n✅ تایید شد")
        else:
            await callback.message.edit_text((callback.message.text or "") + "\n\n✅ تایید شد")
    except Exception:
        pass

    try:
        await callback.bot.send_message(target_user, f"✅ کیف پول شما به مبلغ {amount:,} تومان شارژ شد.")
    except Exception:
        pass
    await callback.answer("تایید شد ✅")


@router.callback_query(F.data.regexp(r"^tx_reject_\d+$"))
async def cb_tx_reject(callback: CallbackQuery, state: FSMContext):
    tx_id = int(callback.data.split("_")[-1])
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "PENDING":
            await callback.answer("این تراکنش قبلاً پردازش شده.", show_alert=True)
            return

    await state.update_data(reject_tx_id=tx_id)
    await state.set_state(RejectReceiptStates.waiting_reason)
    await callback.message.answer(
        "✏️ دلیل رد کردن رسید را ارسال کنید.\n\n"
        "اگر نمی‌خواهید توضیحی برای کاربر ارسال شود، گزینه «رد بدون توضیح» را بزنید.",
        reply_markup=tx_reject_reason_kb(tx_id),
    )
    await callback.answer("دلیل رد رسید را وارد کنید.")


@router.callback_query(F.data.startswith("tx_reject_skip_"))
async def cb_tx_reject_skip(callback: CallbackQuery, state: FSMContext):
    tx_id = int(callback.data.split("_")[-1])
    await _reject_transaction(callback, state, tx_id, None)


@router.callback_query(F.data.startswith("tx_reject_cancel_"))
async def cb_tx_reject_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("لغو شد.")


@router.message(RejectReceiptStates.waiting_reason, F.text)
async def reject_receipt_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    tx_id = data.get("reject_tx_id")
    if not tx_id:
        await state.clear()
        return
    reason = message.text.strip()
    if reason.lower() in {"بدون توضیح", "بدون توضیح.", "skip", "/skip"}:
        reason = None
    await _reject_transaction(message, state, int(tx_id), reason)


async def _reject_transaction(source, state: FSMContext, tx_id: int, reason: str | None):
    async with async_session() as session:
        tx = await session.get(WalletTransaction, tx_id)
        if not tx or tx.status != "PENDING":
            await state.clear()
            if isinstance(source, CallbackQuery):
                await source.answer("این تراکنش قبلاً پردازش شده.", show_alert=True)
            else:
                await source.answer("این تراکنش قبلاً پردازش شده.")
            return
        tx.status = "REJECTED"
        tx.handled_by = source.from_user.id
        target_user = tx.user_id
        await session.commit()

    try:
        admin_message = source.message if isinstance(source, CallbackQuery) else None
        if admin_message and admin_message.photo:
            await admin_message.edit_caption(caption=(admin_message.caption or "") + "\n\n❌ رد شد")
        elif admin_message:
            await admin_message.edit_text((admin_message.text or "") + "\n\n❌ رد شد")
    except Exception:
        pass

    user_text = "❌ متاسفانه رسید واریزی شما تایید نشد. لطفاً با پشتیبانی در ارتباط باشید."
    if reason:
        user_text += f"\n\n📝 دلیل رد: {reason}"

    try:
        await source.bot.send_message(target_user, user_text)
    except Exception:
        pass

    await state.clear()
    if isinstance(source, CallbackQuery):
        await source.answer("رد شد ❌")
    else:
        await source.answer("رسید رد شد ❌")


# ==================== تحویل دستی سفارش‌ها ====================

@router.message(F.text.func(lambda t: bool(t) and t.startswith("/deliver_") and t.split("_", 1)[1].isdigit()))
async def deliver_start(message: Message, state: FSMContext):
    order_id = int(message.text.split("_", 1)[1])
    await state.update_data(order_id=order_id)
    await message.answer("نام کانفیگ را ارسال کنید:")
    await state.set_state(DeliverStates.waiting_config_name)


@router.message(DeliverStates.waiting_config_name)
async def deliver_name(message: Message, state: FSMContext):
    await state.update_data(config_name=message.text.strip())
    await message.answer("حالا لینک کانفیگ/ساب را ارسال کنید:")
    await state.set_state(DeliverStates.waiting_config_link)


@router.message(DeliverStates.waiting_config_link)
async def deliver_link(message: Message, state: FSMContext):
    data = await state.get_data()
    async with async_session() as session:
        order = await session.get(ServiceOrder, data["order_id"])
        if not order:
            await message.answer("❌ سفارش پیدا نشد.")
            await state.clear()
            return
        order.config_name = data["config_name"]
        order.config_link = message.text.strip()
        order.status = "ACTIVE"
        await session.commit()
        user_id, service_name, config_link = order.user_id, order.service_name, order.config_link

    await message.answer("✅ سفارش تحویل داده شد.", reply_markup=admin_main_menu_kb())
    caption = (
        f"🎉 سرویس شما آماده شد!\n\n📦 سرویس: {service_name}\n"
        f"🔑 نام کانفیگ: `{data['config_name']}`\n\n🔗 لینک:\n`{config_link}`"
    )
    try:
        from qr_utils import generate_qr_photo
        await message.bot.send_photo(
            user_id,
            generate_qr_photo(config_link, filename=f"{data['config_name']}.png"),
            caption=caption,
            parse_mode="Markdown",
        )
    except Exception:
        try:
            await message.bot.send_message(user_id, caption, parse_mode="Markdown")
        except Exception:
            pass
    await state.clear()


# ==================== ویرایشگر منوی اصلی ====================

MAIN_KEY_LABELS = {
    "btn_buy": "🛍 خرید سرویس", "btn_tariffs": "🏷 تعرفه‌ها",
    "btn_my_services": "📦 سرویس‌های من", "btn_wallet": "💳 کیف پول",
    "btn_profile": "👤 پروفایل من", "btn_invite": "👥 دعوت دوستان",
    "btn_guide": "📚 آموزش اتصال", "btn_support": "📞 پشتیبانی",
    "btn_free_trial": "🎁 تست رایگان",
}


@router.callback_query(F.data == "admin_menu_editor")
async def cb_menu_editor(callback: CallbackQuery):
    async with async_session() as session:
        buttons = (await session.execute(select(MenuButton).order_by(MenuButton.sort_order))).scalars().all()
        columns = await session.scalar(select(BotContent.value).where(BotContent.key == "menu_columns"))
    columns = columns or "2"

    rows = []
    for b in buttons:
        label = b.label if b.is_custom else MAIN_KEY_LABELS.get(b.key, b.key)
        status = "✅" if b.enabled else "🚫"
        rows.append([InlineKeyboardButton(text=f"{status} {label}", callback_data=f"menuitem_{b.id}")])
    rows.append([
        InlineKeyboardButton(text="➕ افزودن دکمه سفارشی", callback_data="menucustom_new"),
        InlineKeyboardButton(text=f"📐 تعداد ستون: {columns}", callback_data="menucolumns_toggle"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])

    await callback.message.edit_text(
        "🎛 ویرایشگر منوی اصلی\n\n"
        "روی هر دکمه کلیک کن تا فعال/غیرفعالش کنی و گزینه‌های بیشتر (رنگ، ایموجی پرمیوم، "
        "جابه‌جایی، حذف) رو ببینی.\n\n"
        "⚠️ ایموجی پرمیوم و رنگ فقط روی نسخه‌های جدید تلگرام (Bot API 9.4+) نمایش داده میشه؛ "
        "روی نسخه‌های خیلی قدیمی ممکنه فقط متن ساده دیده بشه.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "menucolumns_toggle")
async def cb_menu_columns_toggle(callback: CallbackQuery):
    async with async_session() as session:
        current = await session.scalar(select(BotContent).where(BotContent.key == "menu_columns"))
        new_val = "1" if (current and current.value == "2") else "2"
        if current:
            current.value = new_val
        else:
            session.add(BotContent(key="menu_columns", value=new_val))
        await session.commit()
    await cb_menu_editor(callback)


@router.callback_query(F.data.startswith("menuitem_"))
async def cb_menu_item(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        btn = await session.get(MenuButton, item_id)
    label = btn.label if btn.is_custom else MAIN_KEY_LABELS.get(btn.key, btn.key)
    style_fa = {"primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز", None: "پیش‌فرض"}.get(btn.style, "پیش‌فرض")
    rows = [
        [InlineKeyboardButton(
            text="🚫 غیرفعال کن" if btn.enabled else "✅ فعال کن",
            callback_data=f"menutoggle_{item_id}",
        )],
        [
            InlineKeyboardButton(text="⬆️ بالاتر", callback_data=f"menuup_{item_id}"),
            InlineKeyboardButton(text="⬇️ پایین‌تر", callback_data=f"menudown_{item_id}"),
        ],
        [InlineKeyboardButton(text="🖼 تنظیم آیکون ایموجی پرمیوم", callback_data=f"menuicon_{item_id}")],
        [
            InlineKeyboardButton(text="🔵 آبی", callback_data=f"menustyle_{item_id}_primary"),
            InlineKeyboardButton(text="🟢 سبز", callback_data=f"menustyle_{item_id}_success"),
            InlineKeyboardButton(text="🔴 قرمز", callback_data=f"menustyle_{item_id}_danger"),
            InlineKeyboardButton(text="⚪️ پیش‌فرض", callback_data=f"menustyle_{item_id}_none"),
        ],
    ]
    if btn.is_custom:
        rows.append([InlineKeyboardButton(text="🗑 حذف این دکمه", callback_data=f"menudelete_{item_id}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_menu_editor")])
    await callback.message.edit_text(
        f"دکمه: {label}\nرنگ فعلی: {style_fa}\nآیکون: {'دارد ✅' if btn.icon_custom_emoji_id else 'ندارد'}\n\n"
        f"⚠️ رنگ و آیکون فقط توی نسخه‌های جدید تلگرام (Bot API 9.4+) نمایش داده میشن. "
        f"برای آیکون هم حتماً باید اکانتی که ربات باهاش ساخته شده Telegram Premium داشته باشه.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("menuicon_"))
async def cb_menu_icon(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.split("_")[1])
    await state.update_data(menu_item_id=item_id)
    await callback.message.answer(
        "ایموجی پرمیومی که می‌خوای کنار این دکمه نشون داده بشه رو مستقیم از پنل ایموجی تلگرام بفرست:"
    )
    await state.set_state(MenuEditStates.waiting_icon_emoji)
    await callback.answer()


@router.callback_query(F.data.startswith("menustyle_"))
async def cb_menu_style(callback: CallbackQuery):
    _, item_id, style = callback.data.split("_")
    async with async_session() as session:
        btn = await session.get(MenuButton, int(item_id))
        btn.style = None if style == "none" else style
        await session.commit()
    await cb_menu_item(callback)


@router.callback_query(F.data.startswith("menutoggle_"))
async def cb_menu_toggle(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        btn = await session.get(MenuButton, item_id)
        btn.enabled = not btn.enabled
        await session.commit()
    await cb_menu_editor(callback)


@router.callback_query(F.data.startswith("menuup_") | F.data.startswith("menudown_"))
async def cb_menu_move(callback: CallbackQuery):
    direction_up = callback.data.startswith("menuup_")
    item_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        buttons = (await session.execute(select(MenuButton).order_by(MenuButton.sort_order))).scalars().all()
        idx = next((i for i, b in enumerate(buttons) if b.id == item_id), None)
        if idx is None:
            await callback.answer()
            return
        swap_idx = idx - 1 if direction_up else idx + 1
        if 0 <= swap_idx < len(buttons):
            buttons[idx].sort_order, buttons[swap_idx].sort_order = (
                buttons[swap_idx].sort_order, buttons[idx].sort_order
            )
            await session.commit()
    await cb_menu_editor(callback)


@router.callback_query(F.data.startswith("menudelete_"))
async def cb_menu_delete(callback: CallbackQuery):
    item_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        btn = await session.get(MenuButton, item_id)
        if btn:
            await session.delete(btn)
            await session.commit()
    await cb_menu_editor(callback)


@router.callback_query(F.data == "menucustom_new")
async def cb_menu_custom_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("متن دکمه‌ی جدید رو بفرست (مثلا «⭐️ درباره ما»):")
    await state.set_state(MenuEditStates.waiting_custom_label)
    await callback.answer()


@router.message(MenuEditStates.waiting_custom_label)
async def menu_custom_label_in(message: Message, state: FSMContext):
    if message.text.strip().startswith("/"):
        await message.answer("❌ متن دکمه نمی‌تونه با / شروع بشه. یه متن دیگه بفرست:")
        return
    await state.update_data(label=message.text.strip())
    await message.answer("حالا وقتی کاربر روی این دکمه بزنه، چه پیامی بهش نشون داده بشه؟")
    await state.set_state(MenuEditStates.waiting_custom_response)


@router.message(MenuEditStates.waiting_custom_response)
async def menu_custom_response_in(message: Message, state: FSMContext):
    data = await state.get_data()
    async with async_session() as session:
        max_order = await session.scalar(select(func.max(MenuButton.sort_order)))
        session.add(MenuButton(
            is_custom=True, label=data["label"], response_text=message.text,
            sort_order=(max_order or 0) + 1, enabled=True,
        ))
        await session.commit()
    await message.answer(f"✅ دکمه «{data['label']}» به منوی اصلی اضافه شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== مدیریت/حذف سرویس‌های یک کاربر خاص ====================

@router.callback_query(F.data == "admin_find_user")
async def cb_find_user_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آیدی عددی کاربر مورد نظر رو بفرست (مثلا از لیست کاربران کپی کن):")
    await state.set_state(FindUserStates.waiting_user_id)
    await callback.answer()


@router.message(FindUserStates.waiting_user_id)
async def find_user_process(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد (آیدی عددی) وارد کنید:")
        return
    target_id = int(message.text.strip())

    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == target_id))
        if not user:
            await message.answer("❌ کاربری با این آیدی پیدا نشد.", reply_markup=admin_main_menu_kb())
            await state.clear()
            return

        orders = (await session.execute(
            select(ServiceOrder).where(
                ServiceOrder.user_id == target_id,
                ServiceOrder.status.in_(["ACTIVE", "PENDING_MANUAL"]),
            ).order_by(ServiceOrder.id.desc())
        )).scalars().all()

    await state.clear()

    if not orders:
        await message.answer(
            f"کاربر {target_id} در حال حاضر هیچ سرویس فعالی نداره.",
            reply_markup=admin_main_menu_kb(),
        )
        return

    rows = [[InlineKeyboardButton(text=f"🗑 حذف {o.config_name} ({o.service_name})",
                                   callback_data=f"orderdelete_{o.id}")] for o in orders]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")])
    await message.answer(
        f"سرویس‌های فعال کاربر {target_id}:\n(روی هرکدوم بزنی حذف میشه و کاربر دیگه نمی‌تونه ازش استفاده کنه)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("orderdelete_"))
async def cb_order_delete_confirm(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ بله، حذفش کن", callback_data=f"orderdeleteconfirm_{order_id}"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="admin_main_menu"),
    ]])
    await callback.message.edit_text(
        "⚠️ مطمئنی؟ این سرویس از پنل هم حذف میشه (اگه به پنلی وصل بود) و کاربر دیگه نمی‌تونه ازش استفاده کنه.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("orderdeleteconfirm_"))
async def cb_order_delete_do(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        order = await session.get(ServiceOrder, order_id)
        if not order:
            await callback.answer("پیدا نشد.", show_alert=True)
            return

        panel_error = None
        phantom_error = None

        if order.panel_id:
            panel = await session.get(Panel, order.panel_id)
            if panel:
                try:
                    await delete_panel_account(panel, order.config_name)
                except Exception as e:
                    panel_error = str(e)

        # حذف از PhantomHubs (اگه این سرویس از طریق ادغام ساب ثبت شده بود)
        if order.phantom_token:
            from submerge import delete_from_phantom
            phantom_error = await delete_from_phantom(order.phantom_token)

        order.status = "REMOVED"
        await session.commit()
        user_id, config_name = order.user_id, order.config_name

    errors = ""
    if panel_error:
        errors += f"\n⚠️ حذف از پنل با خطا مواجه شد: {panel_error}"
    if phantom_error:
        errors += f"\n⚠️ حذف از PhantomHubs با خطا مواجه شد: {phantom_error}"

    await callback.message.edit_text(f"✅ سرویس «{config_name}» حذف شد.{errors}", reply_markup=admin_main_menu_kb())
    await callback.answer()

    try:
        await callback.bot.send_message(
            user_id,
            f"⚠️ سرویس «{config_name}» شما توسط پشتیبانی حذف/لغو شد. برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
        )
    except Exception:
        pass


# ==================== پیام به یک کاربر خاص ====================

@router.callback_query(F.data == "admin_message_user")
async def cb_message_user_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آیدی عددی کاربری که می‌خوای بهش پیام بدی رو بفرست:")
    await state.set_state(MessageUserStates.waiting_user_id)
    await callback.answer()


@router.message(MessageUserStates.waiting_user_id)
async def message_user_id_in(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد (آیدی عددی) وارد کنید:")
        return
    target_id = int(message.text.strip())
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == target_id))
    if not user:
        await message.answer("❌ کاربری با این آیدی پیدا نشد.", reply_markup=admin_main_menu_kb())
        await state.clear()
        return
    await state.update_data(target_id=target_id)
    await message.answer(f"متن پیام برای کاربر {target_id} رو بفرست:")
    await state.set_state(MessageUserStates.waiting_message)


@router.message(MessageUserStates.waiting_message)
async def message_user_send(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data["target_id"]
    try:
        await message.bot.send_message(target_id, message.text, entities=message.entities)
        await message.answer("✅ پیام ارسال شد.", reply_markup=admin_main_menu_kb())
    except Exception as e:
        await message.answer(f"❌ ارسال پیام fail شد: {e}", reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== شارژ/برداشت دستی کیف پول ====================

@router.callback_query(F.data == "admin_wallet_adjust")
async def cb_wallet_adjust_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آیدی عددی کاربر مورد نظر رو بفرست:")
    await state.set_state(WalletAdjustStates.waiting_user_id)
    await callback.answer()


@router.message(WalletAdjustStates.waiting_user_id)
async def wallet_adjust_id_in(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ فقط عدد (آیدی عددی) وارد کنید:")
        return
    target_id = int(message.text.strip())
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == target_id))
    if not user:
        await message.answer("❌ کاربری با این آیدی پیدا نشد.", reply_markup=admin_main_menu_kb())
        await state.clear()
        return
    await state.update_data(target_id=target_id)
    await message.answer(
        f"موجودی فعلی کاربر {target_id}: {user.balance:,} تومان\n\n"
        f"مبلغ رو وارد کن. برای شارژ عدد مثبت (مثلا 50000)، برای برداشت عدد منفی (مثلا -50000-):"
    )
    await state.set_state(WalletAdjustStates.waiting_amount)


@router.message(WalletAdjustStates.waiting_amount)
async def wallet_adjust_amount_in(message: Message, state: FSMContext):
    text = message.text.strip().replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        await message.answer("❌ فقط عدد وارد کنید (مثبت برای شارژ، منفی برای برداشت):")
        return
    await state.update_data(amount=amount)
    await message.answer("یه توضیح کوتاه برای این تراکنش بنویس (مثلا «جبران خطا» یا «هدیه»):")
    await state.set_state(WalletAdjustStates.waiting_note)


@router.message(WalletAdjustStates.waiting_note)
async def wallet_adjust_note_in(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id, amount = data["target_id"], data["amount"]
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.user_id == target_id))
        user.balance += amount
        session.add(WalletTransaction(
            user_id=target_id, amount=amount, transaction_type="BONUS" if amount > 0 else "REFUND",
            method="ADMIN", status="SUCCESS", description=f"تنظیم دستی توسط ادمین: {message.text}",
            handled_by=message.from_user.id,
        ))
        await session.commit()
        new_balance = user.balance

    verb = "شارژ" if amount > 0 else "برداشت"
    await message.answer(
        f"✅ {verb} انجام شد.\nموجودی جدید کاربر {target_id}: {new_balance:,} تومان",
        reply_markup=admin_main_menu_kb(),
    )
    try:
        await message.bot.send_message(
            target_id,
            f"💰 موجودی کیف پول شما توسط پشتیبانی {'افزایش' if amount > 0 else 'کاهش'} یافت "
            f"({abs(amount):,} تومان).\nموجودی جدید: {new_balance:,} تومان",
        )
    except Exception:
        pass
    await state.clear()


# ==================== تنظیمات ادغام ساب ====================

@router.callback_query(F.data == "admin_submerge")
async def cb_submerge_settings(callback: CallbackQuery):
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        if not cfg:
            cfg = SubMergeConfig()
            session.add(cfg)
            await session.commit()
            await session.refresh(cfg)

    status = "فعال ✅" if cfg.active else "غیرفعال ❌"
    token_status = "تنظیم شده ✅" if cfg.sync_token else "تنظیم نشده ❌"
    text = (
        f"🔗 تنظیمات ادغام ساب (PhantomHubs)\n\n"
        f"وضعیت: {status}\n"
        f"آدرس پایه: {cfg.base_url or '(تنظیم نشده)'}\n"
        f"PANEL_SYNC_TOKEN: {token_status}\n"
        f"نام نمایشی: {cfg.display_name}\n"
        f"کانال پشتیبانی: {cfg.support_channel}\n\n"
        f"✅ این بخش الان بر اساس مستندات رسمی API پیاده شده. اگه بازم fail بشه، لینک خام "
        f"به مشتری داده میشه و متن دقیق خطا برات پیام میشه."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ تنظیم آدرس پایه", callback_data="submerge_seturl")],
        [InlineKeyboardButton(text="🔑 تنظیم PANEL_SYNC_TOKEN", callback_data="submerge_settoken")],
        [InlineKeyboardButton(text="✏️ تنظیم نام نمایشی (بالای اسم ساب)", callback_data="submerge_setname")],
        [InlineKeyboardButton(text="✏️ تنظیم کانال پشتیبانی", callback_data="submerge_setchannel")],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کن" if cfg.active else "✅ فعال کن",
            callback_data="submerge_toggle",
        )],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "submerge_toggle")
async def cb_submerge_toggle(callback: CallbackQuery):
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        cfg.active = not cfg.active
        await session.commit()
    await cb_submerge_settings(callback)


@router.callback_query(F.data == "submerge_setname")
async def cb_submerge_setname(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "نام نمایشی جدید رو بفرست (همونی که بالای اسم ساب توی اپلیکیشن‌های کلاینت مثل "
        "Hiddify/V2RayNG نشون داده میشه، مثلا @LidsoNet):\n\n"
        "⚠️ این تغییر فقط روی سرویس‌های جدید اعمال میشه؛ سرویس‌های قبلی که قبلاً ساخته شدن "
        "همون نام قبلی رو دارن (باید توکنشون دوباره آپدیت بشه که فعلاً از این بات پشتیبانی نمیشه)."
    )
    await state.set_state(SubMergeStates.waiting_display_name)
    await callback.answer()


@router.message(SubMergeStates.waiting_display_name)
async def submerge_display_name_in(message: Message, state: FSMContext):
    name = message.text.strip()
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        if not cfg:
            cfg = SubMergeConfig()
            session.add(cfg)
        cfg.display_name = name
        await session.commit()
    await message.answer(f"✅ نام نمایشی به «{name}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "submerge_setchannel")
async def cb_submerge_setchannel(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آیدی کانال پشتیبانی جدید رو بفرست (مثلا @LidsoNet):")
    await state.set_state(SubMergeStates.waiting_support_channel)
    await callback.answer()


@router.message(SubMergeStates.waiting_support_channel)
async def submerge_support_channel_in(message: Message, state: FSMContext):
    channel = message.text.strip()
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        if not cfg:
            cfg = SubMergeConfig()
            session.add(cfg)
        cfg.support_channel = channel
        await session.commit()
    await message.answer(f"✅ کانال پشتیبانی به «{channel}» تغییر کرد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "submerge_seturl")
async def cb_submerge_seturl(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آدرس پایه‌ی ابزار ادغام ساب رو بفرست (مثلا https://api.phantomhubs.shop):")
    await state.set_state(SubMergeStates.waiting_base_url)
    await callback.answer()


@router.message(SubMergeStates.waiting_base_url)
async def submerge_url_in(message: Message, state: FSMContext):
    url = message.text.strip().rstrip("/")
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        if not cfg:
            cfg = SubMergeConfig()
            session.add(cfg)
        cfg.base_url = url
        await session.commit()
    await message.answer(f"✅ آدرس ذخیره شد: {url}", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "submerge_settoken")
async def cb_submerge_settoken(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "PANEL_SYNC_TOKEN رو بفرست (همون توکنی که از سازنده‌ی ابزار ادغام ساب گرفتی):\n"
        "(بعد از ثبت، پیامت حذف میشه)"
    )
    await state.set_state(SubMergeStates.waiting_username)  # از همین state برای توکن استفاده می‌کنیم
    await callback.answer()


@router.message(SubMergeStates.waiting_username)
async def submerge_token_in(message: Message, state: FSMContext):
    token = message.text.strip()
    async with async_session() as session:
        cfg = await session.scalar(select(SubMergeConfig).limit(1))
        if not cfg:
            cfg = SubMergeConfig()
            session.add(cfg)
        cfg.sync_token = token
        await session.commit()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ توکن ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()
    await state.clear()


# ==================== تنظیمات درگاه پرداخت (HooshPay) ====================

@router.callback_query(F.data == "admin_gateway")
async def cb_gateway_settings(callback: CallbackQuery):
    async with async_session() as session:
        gw = await session.scalar(select(PaymentGatewayConfig).limit(1))
        if not gw:
            gw = PaymentGatewayConfig()
            session.add(gw)
            await session.commit()
            await session.refresh(gw)

    status = "فعال ✅" if gw.active else "غیرفعال ❌"
    key_status = "تنظیم شده ✅" if gw.api_key else "تنظیم نشده ❌"
    fee_fa = {"seller": "از فروشنده", "buyer": "از خریدار", "split": "نصف‌نصف"}.get(gw.fee_mode, gw.fee_mode)
    text = (
        f"🌐 تنظیمات درگاه پرداخت (HooshPay)\n\n"
        f"وضعیت: {status}\n"
        f"آدرس پایه: {gw.base_url}\n"
        f"کلید API: {key_status}\n"
        f"روش کارمزد: {fee_fa}\n\n"
        f"ℹ️ چون بات فعلاً روی سیستم شخصی اجرا میشه (بدون دامنه‌ی عمومی)، به‌جای webhook، "
        f"هر {GATEWAY_POLL_TEXT} وضعیت فاکتورهای در انتظار خودکار چک میشه."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 تنظیم کلید API", callback_data="gw_setkey")],
        [InlineKeyboardButton(text="🔒 تنظیم Secret", callback_data="gw_setsecret")],
        [
            InlineKeyboardButton(text="👤 کارمزد از فروشنده", callback_data="gw_fee_seller"),
            InlineKeyboardButton(text="🛍 کارمزد از خریدار", callback_data="gw_fee_buyer"),
            InlineKeyboardButton(text="➗ نصف‌نصف", callback_data="gw_fee_split"),
        ],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کن" if gw.active else "✅ فعال کن",
            callback_data="gw_toggle",
        )],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


GATEWAY_POLL_TEXT = "۱۵ ثانیه"


@router.callback_query(F.data == "gw_toggle")
async def cb_gateway_toggle(callback: CallbackQuery):
    async with async_session() as session:
        gw = await session.scalar(select(PaymentGatewayConfig).limit(1))
        gw.active = not gw.active
        await session.commit()
    await cb_gateway_settings(callback)


@router.callback_query(F.data.startswith("gw_fee_"))
async def cb_gateway_fee(callback: CallbackQuery):
    mode = callback.data.split("_")[-1]
    async with async_session() as session:
        gw = await session.scalar(select(PaymentGatewayConfig).limit(1))
        gw.fee_mode = mode
        await session.commit()
    await cb_gateway_settings(callback)


@router.callback_query(F.data == "gw_setkey")
async def cb_gateway_setkey(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("کلید API هوش‌پی رو بفرست (مثلا hp_live_...):\n(بعد از ثبت، پیامت حذف میشه)")
    await state.set_state(GatewayConfigStates.waiting_api_key)
    await callback.answer()


@router.message(GatewayConfigStates.waiting_api_key)
async def gateway_key_in(message: Message, state: FSMContext):
    key = message.text.strip()
    async with async_session() as session:
        gw = await session.scalar(select(PaymentGatewayConfig).limit(1))
        if not gw:
            gw = PaymentGatewayConfig()
            session.add(gw)
        gw.api_key = key
        await session.commit()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ کلید API ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "gw_setsecret")
async def cb_gateway_setsecret(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Secret هوش‌پی رو بفرست:\n(بعد از ثبت، پیامت حذف میشه)")
    await state.set_state(GatewayConfigStates.waiting_api_secret)
    await callback.answer()


@router.message(GatewayConfigStates.waiting_api_secret)
async def gateway_secret_in(message: Message, state: FSMContext):
    secret = message.text.strip()
    async with async_session() as session:
        gw = await session.scalar(select(PaymentGatewayConfig).limit(1))
        if not gw:
            gw = PaymentGatewayConfig()
            session.add(gw)
        gw.api_secret = secret
        await session.commit()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ Secret ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


# ==================== تنظیمات ولت‌های ارزی (گرام/TON و USDC/BEP20) ====================

@router.callback_query(F.data == "admin_crypto")
async def cb_crypto_settings(callback: CallbackQuery):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        if not cc:
            cc = CryptoConfig()
            session.add(cc)
            await session.commit()
            await session.refresh(cc)

    comment_status = "فعال ✅" if cc.comment_enabled else "غیرفعال ❌"
    text = (
        f"🪙 تنظیمات ولت‌های ارزی\n\n"
        f"💎 آدرس گرام (TON): {cc.ton_address or '(تنظیم نشده)'}\n"
        f"💵 آدرس USDC (BEP20): {cc.bsc_address or '(تنظیم نشده)'}\n"
        f"🔑 کلید TonCenter: {'دارد ✅' if cc.ton_api_key else 'ندارد ❌'}\n"
        f"🔑 کلید BscScan: {'دارد ✅' if cc.bscscan_api_key else 'ندارد ❌'}\n"
        f"📝 کامنت تراکنش (فقط گرام): {comment_status}\n\n"
        f"ℹ️ کلید TonCenter رو از ربات @tonapibot و کلید BscScan رو از bscscan.com رایگان بگیر.\n"
        f"⚠️ توجه: کامنت فقط روی شبکه TON معنی داره؛ USDC (BEP20) اصلاً از کامنت پشتیبانی نمی‌کنه."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 تنظیم آدرس گرام (TON)", callback_data="crypto_setton")],
        [InlineKeyboardButton(text="💵 تنظیم آدرس USDC (BEP20)", callback_data="crypto_setbsc")],
        [InlineKeyboardButton(text="🔑 تنظیم کلید TonCenter", callback_data="crypto_settonkey")],
        [InlineKeyboardButton(text="🔑 تنظیم کلید BscScan", callback_data="crypto_setbsckey")],
        [InlineKeyboardButton(
            text="🚫 غیرفعال کردن کامنت" if cc.comment_enabled else "✅ فعال کردن کامنت",
            callback_data="crypto_toggle_comment",
        )],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_main_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "crypto_toggle_comment")
async def cb_crypto_toggle_comment(callback: CallbackQuery):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        cc.comment_enabled = not cc.comment_enabled
        await session.commit()
    await cb_crypto_settings(callback)


@router.callback_query(F.data == "crypto_setton")
async def cb_crypto_setton(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آدرس ولت Tonkeeper (گرام/TON) رو بفرست:")
    await state.set_state(CryptoConfigStates.waiting_ton_address)
    await callback.answer()


@router.message(CryptoConfigStates.waiting_ton_address)
async def crypto_ton_address_in(message: Message, state: FSMContext):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        cc.ton_address = message.text.strip()
        await session.commit()
    await message.answer("✅ آدرس گرام ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "crypto_setbsc")
async def cb_crypto_setbsc(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("آدرس ولت Trust Wallet (USDC روی BEP20) رو بفرست:")
    await state.set_state(CryptoConfigStates.waiting_bsc_address)
    await callback.answer()


@router.message(CryptoConfigStates.waiting_bsc_address)
async def crypto_bsc_address_in(message: Message, state: FSMContext):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        cc.bsc_address = message.text.strip()
        await session.commit()
    await message.answer("✅ آدرس USDC ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "crypto_settonkey")
async def cb_crypto_settonkey(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "کلید TonCenter رو بفرست (از @tonapibot توی تلگرام رایگان بگیر):\n(بعد از ثبت، پیامت حذف میشه)"
    )
    await state.set_state(CryptoConfigStates.waiting_ton_api_key)
    await callback.answer()


@router.message(CryptoConfigStates.waiting_ton_api_key)
async def crypto_ton_key_in(message: Message, state: FSMContext):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        cc.ton_api_key = message.text.strip()
        await session.commit()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ کلید TonCenter ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()


@router.callback_query(F.data == "crypto_setbsckey")
async def cb_crypto_setbsckey(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "کلید BscScan رو بفرست (از bscscan.com رایگان بگیر):\n(بعد از ثبت، پیامت حذف میشه)"
    )
    await state.set_state(CryptoConfigStates.waiting_bscscan_api_key)
    await callback.answer()


@router.message(CryptoConfigStates.waiting_bscscan_api_key)
async def crypto_bscscan_key_in(message: Message, state: FSMContext):
    async with async_session() as session:
        cc = await session.scalar(select(CryptoConfig).limit(1))
        cc.bscscan_api_key = message.text.strip()
        await session.commit()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ کلید BscScan ذخیره شد.", reply_markup=admin_main_menu_kb())
    await state.clear()
