import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import Message
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from database import (
    async_session, User, StockConfig, ServiceOrder, ServicePlan, Panel, WalletTransaction,
    Category, BotContent, CategoryDuration, DiscountCode, UsedDiscount,
)
from keyboards.user_kb import (
    main_keyboard, wallet_menu_keyboard, services_menu_keyboard,
    durations_keyboard, plans_keyboard,
)
from states.user_states import ShopStates
from ui_texts import AnyButtonText
from panels import get_next_config_name, volume_tag_from_plan, create_panel_account
from operation_locks import user_operation_lock, service_creation_lock
from submerge import apply_sub_merge
import config as cfg

logger = logging.getLogger(__name__)
router = Router()


async def get_content(session, key, default=""):
    val = await session.scalar(select(BotContent.value).where(BotContent.key == key))
    return val or default


# ==================== ناوبری ====================

@router.message(AnyButtonText("btn_buy", "btn_back_services"))
async def shop_menu(message: Message, state: FSMContext):
    async with async_session() as session:
        categories = (await session.execute(
            select(Category).where(Category.active == True).order_by(Category.sort_order)
        )).scalars().all()

    if not categories:
        await message.answer("⚠️ در حال حاضر سرویسی برای فروش تعریف نشده.", reply_markup=await main_keyboard())
        await state.clear()
        return

    await state.set_state(ShopStates.choosing_category)
    await message.answer("لطفاً نوع سرویس مورد نظر را انتخاب کنید:",
                          reply_markup=await services_menu_keyboard(categories))


@router.message(ShopStates.choosing_category)
async def category_select(message: Message, state: FSMContext):
    async with async_session() as session:
        category = await session.scalar(
            select(Category).where(Category.title == message.text, Category.active == True)
        )
        if not category:
            raise SkipHandler

        durations = (await session.execute(
            select(CategoryDuration).where(
                CategoryDuration.category_id == category.id, CategoryDuration.active == True
            ).order_by(CategoryDuration.sort_order)
        )).scalars().all()

    if not durations:
        await message.answer("⚠️ برای این سرویس هنوز مدت‌زمانی تعریف نشده.", reply_markup=await main_keyboard())
        await state.clear()
        return

    await state.update_data(cat=category.prefix, category_id=category.id)
    await state.set_state(ShopStates.choosing_duration)
    await message.answer("مدت زمان سرویس را انتخاب کنید:", reply_markup=await durations_keyboard(durations))


@router.message(ShopStates.choosing_duration)
async def duration_select(message: Message, state: FSMContext):
    data = await state.get_data()
    category_id = data.get("category_id")
    cat = data.get("cat")

    async with async_session() as session:
        duration = await session.scalar(
            select(CategoryDuration).where(
                CategoryDuration.category_id == category_id,
                CategoryDuration.label == message.text,
                CategoryDuration.active == True,
            )
        )
        if not duration:
            raise SkipHandler

        plans = (await session.execute(
            select(ServicePlan).where(
                ServicePlan.category == cat, ServicePlan.duration_id == duration.id,
                ServicePlan.active == True,
            ).order_by(ServicePlan.sort_order)
        )).scalars().all()

    if not plans:
        await message.answer("⚠️ در حال حاضر پلنی برای این مدت زمان تعریف نشده.", reply_markup=await main_keyboard())
        await state.clear()
        return

    await state.update_data(duration_id=duration.id)
    await state.set_state(ShopStates.choosing_plan)
    await message.answer("حجم / نوع مورد نظر را انتخاب کنید:", reply_markup=await plans_keyboard(plans))


# ==================== خرید ====================

async def _notify_admins(bot, text):
    for admin_id in cfg.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def _notify_admins_manual_order(bot, order_id, plan_name, user_id, username=None, full_name=None, extra=""):
    uname = f"@{username}" if username else "بدون یوزرنیم"
    await _notify_admins(
        bot,
        f"🆕 سفارش نیازمند تحویل دستی\n\n"
        f"شماره سفارش: #{order_id}\n"
        f"سرویس: {plan_name}\n"
        f"🆔 آیدی عددی: {user_id}\n"
        f"✏️ یوزرنیم: {uname}\n"
        f"👤 نام: {full_name or '-'}\n"
        f"{extra}\n\n"
        f"برای تحویل از دستور زیر استفاده کنید:\n"
        f"/deliver_{order_id}"
    )

@router.message(ShopStates.choosing_plan, F.text.contains("|"))
async def process_purchase(message: Message, state: FSMContext):
    """پردازش خرید بدون نگه‌داشتن FSM/DB transaction در زمان تماس با پنل.

    نکات مهم:
    - بعد از snapshot شدن اطلاعات خرید، FSM همان لحظه آزاد می‌شود تا کاربر بتواند
      همزمان با ساخت سرویس با ربات کار کند.
    - رزرو موجودی و ساخت سفارش اولیه فقط در یک تراکنش کوتاه انجام می‌شود.
    - تماس با پنل بیرون از تراکنش DB انجام می‌شود.
    - فقط بخش «انتخاب نام + ساخت کاربر روی پنل» برای همان نوع سرویس سری می‌شود.
    - هیچ‌وقت لینک کاربر قبلی در collision تحویل داده نمی‌شود.
    """
    data = await state.get_data()
    cat = data.get("cat")
    duration_id = data.get("duration_id")
    if not cat:
        raise SkipHandler

    plan_name = message.text.split("|")[0].strip()
    user_id = message.from_user.id

    # Snapshot کردن state و آزاد کردن FSM قبل از هر عملیات طولانی.
    # await state.clear()

    order_id = None
    plan_id = None
    panel_id = None
    final_price = 0
    panel = None
    plan = None
    stock_order = None
    pending_manual_order = None
    processing_msg = None

    # ------------------------------------------------------------
    # مرحله ۱: رزرو خرید در DB؛ کوتاه و فقط با lock همان کاربر
    # ------------------------------------------------------------
    async with user_operation_lock(user_id):
        async with async_session() as session:
            plan = await session.scalar(
                select(ServicePlan).where(
                    ServicePlan.category == cat,
                    ServicePlan.duration_id == duration_id,
                    ServicePlan.name == plan_name,
                    ServicePlan.active == True,
                )
            )
            if not plan:
                await message.answer("❌ این پلن دیگر موجود نیست.")
                return

            plan_id = plan.id
            panel_id = plan.panel_id

            user = await session.scalar(select(User).where(User.user_id == user_id))
            if not user:
                await message.answer("❌ اطلاعات حساب شما پیدا نشد. لطفا /start را بزنید.")
                return

            discount_percent = user.pending_discount_percent
            final_price = plan.price
            if discount_percent:
                final_price = int(round(plan.price * (100 - discount_percent) / 100))

            if user.balance < final_price:
                await message.answer(
                    f"❌ موجودی کیف پول کافی نیست.\n\n"
                    f"💰 موجودی فعلی: {user.balance:,} تومان\n"
                    f"💵 مبلغ مورد نیاز: {final_price:,} تومان\n\n"
                    f"برای افزایش موجودی وارد بخش «کیف پول» شوید.",
                    reply_markup=await wallet_menu_keyboard(),
                )
                return

            # کسر موجودی در تراکنش کوتاه
            user.balance -= final_price
            user.total_purchases += 1
            if plan.volume_gb and plan.volume_gb > 0:
                user.total_volume += plan.volume_gb
            else:
                user.total_unlimited_purchases += 1
            user.total_spent += final_price

            session.add(WalletTransaction(
                user_id=user_id,
                amount=final_price,
                transaction_type="PURCHASE",
                method="WALLET",
                status="SUCCESS",
                description=plan.name,
            ))

            if discount_percent:
                used_promo_code = user.pending_discount_code
                user.pending_discount_code = None
                user.pending_discount_percent = None
                promo_row = await session.scalar(
                    select(DiscountCode).where(DiscountCode.code == used_promo_code)
                )
                if promo_row:
                    promo_row.current_uses += 1
                session.add(UsedDiscount(user_id=user_id, code=used_promo_code))

            # انبار: کاملاً مستقل از ساخت پنل
            stock = await session.scalar(
                select(StockConfig).where(
                    StockConfig.service_id == plan.id,
                    StockConfig.status == "AVAILABLE",
                )
            )

            expire_at = None if plan.duration_days == 0 else datetime.now(timezone.utc) + timedelta(days=plan.duration_days)

            if stock:
                stock.status = "SOLD"
                stock.assigned_user = user_id
                stock.sold_at = datetime.now(timezone.utc)
                stock_order = ServiceOrder(
                    user_id=user_id,
                    plan_id=plan.id,
                    service_name=plan.name,
                    config_name=stock.config_name or "-",
                    config_link=stock.config_link,
                    price=final_price,
                    inventory_id=stock.id,
                    panel_id=stock.panel_id,
                    status="ACTIVE",
                    expire_at=expire_at,
                )
                session.add(stock_order)
                await session.commit()
                order_id = stock_order.id

            elif plan.delivery_mode == "MANUAL":
                pending_manual_order = ServiceOrder(
                    user_id=user_id,
                    plan_id=plan.id,
                    service_name=plan.name,
                    config_name="در حال آماده‌سازی",
                    config_link="در حال آماده‌سازی",
                    price=final_price,
                    status="PENDING_MANUAL",
                    expire_at=expire_at,
                )
                session.add(pending_manual_order)
                await session.commit()
                order_id = pending_manual_order.id

            else:
                # سفارش AUTO را قبل از تماس پنل ثبت می‌کنیم تا حتی اگر پنل fail شد
                # سفارش گم نشود و بتوان آن را دستی تحویل داد.
                pending_manual_order = ServiceOrder(
                    user_id=user_id,
                    plan_id=plan.id,
                    service_name=plan.name,
                    config_name="در حال آماده‌سازی",
                    config_link="در حال آماده‌سازی",
                    price=final_price,
                    panel_id=panel.id if (panel := await session.get(Panel, plan.panel_id)) else None,
                    status="PENDING_MANUAL",
                    expire_at=expire_at,
                )
                session.add(pending_manual_order)
                await session.commit()
                order_id = pending_manual_order.id

    # ------------------------------------------------------------
    # مرحله ۲: موارد سریع (انبار / دستی)
    # ------------------------------------------------------------
    if stock_order:
        order = stock_order
        caption = (
            f"🎉 خرید با موفقیت انجام شد!\n\n"
            f"📦 سرویس: {plan_name}\n"
            f"🔑 نام کانفیگ: `{order.config_name}`\n\n"
            f"🔗 لینک اتصال:\n`{order.config_link}`\n\n"
            f"📱 می‌تونی از روی QR کد بالا هم مستقیم اسکن و وصل بشی."
        )
        try:
            from qr_utils import generate_qr_photo
            await message.answer_photo(
                generate_qr_photo(order.config_link, filename=f"{order.config_name}.png"),
                caption=caption, parse_mode="Markdown", reply_markup=await main_keyboard()
            )
        except Exception as e:
            logger.warning(f"ساخت QR کد برای سفارش {order.id} fail شد: {e}")
            await message.answer(caption, parse_mode="Markdown", reply_markup=await main_keyboard())
        await _notify_admins(message.bot, f"🛒 خرید جدید انجام شد\n\n🆔 آیدی عددی: {user_id}\nسرویس: {plan_name}\nقیمت: {final_price:,} تومان\nنام کانفیگ: {order.config_name}\nشماره سفارش: #{order.id}")
        return

    if plan.delivery_mode == "MANUAL":
        await message.answer(
            f"✅ سفارش شما ثبت شد!\n\n📦 سرویس: {plan_name}\n"
            f"⏳ سرویس شما به زودی توسط پشتیبانی به صورت دستی تحویل داده می‌شود.",
            reply_markup=await main_keyboard(),
        )
        await _notify_admins_manual_order(
            message.bot, order_id, plan.name, user_id,
            message.from_user.username, message.from_user.full_name
        )
        return

    # ------------------------------------------------------------
    # مرحله ۳: AUTO؛ فقط بخش حساس پنل سری می‌شود
    # ------------------------------------------------------------
    processing_text = "⏳ سفارش شما در حال آماده‌سازیه، چند لحظه صبر کنید..."
    try:
        async with async_session() as session:
            processing_text = await get_content(session, "order_processing_text", processing_text)
            panel = await session.get(Panel, panel_id) if panel_id else None
    except Exception as e:
        logger.error(f"خطا در خواندن پنل سفارش #{order_id}: {e}")
        panel = None

    processing_msg = await message.answer(processing_text, reply_markup=await main_keyboard())

    config_link = None
    config_name = None
    phantom_token = None
    build_error = None

    if not panel:
        build_error = "این پلن به هیچ پنلی وصل نیست."
    else:
        volume_tag = volume_tag_from_plan(plan)
        # این lock فقط برای نام‌گذاری/ساخت روی همان پنل و همان نوع سرویس است.
        async with service_creation_lock(panel.id, cat, volume_tag):
            collision_error = None
            for attempt in range(8):
                if attempt == 0:
                    config_name = await get_next_config_name(cat, volume_tag, panel)
                else:
                    try:
                        prefix, num_text = config_name.rsplit("_", 1)
                        config_name = f"{prefix}_{int(num_text) + 1}"
                    except Exception:
                        config_name = await get_next_config_name(cat, volume_tag, panel)

                try:
                    config_link = await create_panel_account(panel, config_name, plan)
                    if not config_link or not str(config_link).startswith((
                        "http://", "https://", "vless://", "vmess://", "trojan://", "ss://"
                    )):
                        raise ValueError(f"پاسخ پنل معتبر به نظر نمی‌رسه: {config_link!r}")

                    config_link, submerge_error = await apply_sub_merge(
                        config_link, plan, config_name, user_id, panel=panel
                    )
                    phantom_token = config_link.split("/token/")[-1] if "/token/" in config_link else None
                    if submerge_error:
                        await _notify_admins(
                            message.bot,
                            f"⚠️ ادغام ساب برای سفارش کاربر {user_id} fail شد (لینک خام تحویل داده شد):\n\n{submerge_error}"
                        )
                    build_error = None
                    break
                except Exception as e:
                    error_text = str(e)
                    if "__CONFIG_NAME_COLLISION__" in error_text or "409" in error_text:
                        collision_error = error_text
                        logger.warning(
                            f"⚠️ collision برای نام {config_name} روی پنل «{panel.name}»، "
                            f"تلاش بعدی ({attempt + 1}/8)"
                        )
                        build_error = error_text
                        continue
                    build_error = error_text
                    logger.exception(f"خطا در ساخت کانفیگ سفارش #{order_id} روی پنل «{panel.name}»")
                    break
            else:
                build_error = collision_error or build_error or "ساخت کانفیگ پس از چند تلاش ناموفق بود."

    try:
        await processing_msg.delete()
    except Exception:
        pass

    # ------------------------------------------------------------
    # مرحله ۴: نتیجه را در یک تراکنش مستقل ذخیره کن
    # ------------------------------------------------------------
    if build_error or not config_link or not config_name:
        async with async_session() as session:
            order = await session.get(ServiceOrder, order_id)
            if order:
                order.config_name = "در حال آماده‌سازی"
                order.config_link = "در حال آماده‌سازی"
                order.status = "PENDING_MANUAL"
                await session.commit()

        await message.answer(
            f"✅ سفارش شما ثبت شد!\n\n📦 سرویس: {plan_name}\n"
            f"⏳ به دلیل یک مشکل فنی، سرویس شما طی چند دقیقه توسط پشتیبانی به صورت دستی تحویل داده می‌شود.",
            reply_markup=await main_keyboard(),
        )
        await _notify_admins_manual_order(
            message.bot, order_id, plan.name, user_id,
            message.from_user.username, message.from_user.full_name,
            extra=f"⚠️ دلیل نیاز به تحویل دستی: {build_error or 'لینک نامعتبر/خالی'}"
        )
        return

    async with async_session() as session:
        order = await session.get(ServiceOrder, order_id)
        if not order:
            await message.answer(
                "⚠️ سرویس ساخته شد اما ثبت نهایی سفارش با مشکل مواجه شد. پشتیبانی آن را بررسی می‌کند.",
                reply_markup=await main_keyboard(),
            )
            await _notify_admins(
                message.bot,
                f"🚨 سفارش #{order_id} ساخته شد ولی رکورد سفارش پیدا نشد. کاربر: {user_id} | کانفیگ: {config_name}"
            )
            return
        order.config_name = config_name
        order.config_link = config_link
        order.status = "ACTIVE"
        order.phantom_token = phantom_token
        await session.commit()

    caption = (
        f"🎉 خرید با موفقیت انجام شد!\n\n"
        f"📦 سرویس: {plan_name}\n"
        f"🔑 نام کانفیگ: `{config_name}`\n\n"
        f"🔗 لینک اتصال:\n`{config_link}`\n\n"
        f"📱 می‌تونی از روی QR کد بالا هم مستقیم اسکن و وصل بشی."
    )
    try:
        from qr_utils import generate_qr_photo
        await message.answer_photo(
            generate_qr_photo(config_link, filename=f"{config_name}.png"),
            caption=caption, parse_mode="Markdown", reply_markup=await main_keyboard()
        )
    except Exception as e:
        logger.warning(f"ساخت QR کد برای سفارش {order_id} fail شد: {e}")
        await message.answer(caption, parse_mode="Markdown", reply_markup=await main_keyboard())

    await _notify_admins(
        message.bot,
        f"🛒 خرید جدید انجام شد\n\n"
        f"🆔 آیدی عددی: {user_id}\n"
        f"✏️ یوزرنیم: @{message.from_user.username if message.from_user.username else 'بدون یوزرنیم'}\n"
        f"👤 نام: {message.from_user.full_name}\n"
        f"سرویس: {plan_name}\nقیمت: {final_price:,} تومان\n"
        f"نام کانفیگ: {config_name}\nشماره سفارش: #{order_id}",
    )

