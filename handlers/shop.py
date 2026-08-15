import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.types import Message
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
            return  # پیام نامرتبط - نادیده گرفته میشه (مثلا "تمدید اشتراک" که جای دیگه هندل میشه)

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
            return  # پیام نامرتبط - نادیده گرفته میشه

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

@router.message(ShopStates.choosing_plan)
async def process_purchase(message: Message, state: FSMContext):
    if "|" not in (message.text or ""):
        return

    data = await state.get_data()
    cat = data.get("cat")
    duration_id = data.get("duration_id")
    if not cat:
        return

    plan_name = message.text.split("|")[0].strip()
    user_id = message.from_user.id

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

        user = await session.scalar(select(User).where(User.user_id == user_id))
        if not user:
            await message.answer("❌ اطلاعات حساب شما پیدا نشد. لطفا /start را بزنید.")
            return

        # ---- اعمال کد تخفیف در صورت وجود ----
        final_price = plan.price
        discount_percent = user.pending_discount_percent

        if discount_percent:
            final_price = int(round(plan.price * (100 - discount_percent) / 100))

        if user.balance < final_price:
            need_text = (
                f"❌ موجودی کیف پول کافی نیست.\n\n"
                f"💰 موجودی فعلی: {user.balance:,} تومان\n"
                f"💵 مبلغ مورد نیاز: {final_price:,} تومان\n\n"
                f"برای افزایش موجودی وارد بخش «کیف پول» شوید."
            )
            
            # خیلی مهم: از حالت انتخاب/خرید سرویس خارج شو
            await state.clear()

            await message.answer(need_text, reply_markup=await wallet_menu_keyboard())
            return

        # ---- کسر موجودی و ثبت آمار ----
        user.balance -= final_price
        user.total_purchases += 1
        if plan.category != "LidsoUnlimited" and plan.volume_gb and plan.volume_gb > 0:
            user.total_volume += plan.volume_gb
        else:
            # Unlimited حتی اگر Data Limit فنی داشته باشد، در آمار حجم گیگابایتی حساب نمی‌شود.
            user.total_unlimited_purchases += 1
        user.total_spent += final_price

        session.add(WalletTransaction(
            user_id=user_id, amount=final_price, transaction_type="PURCHASE",
            method="WALLET", status="SUCCESS", description=plan.name,
        ))

        if discount_percent:
            used_promo_code = user.pending_discount_code
            user.pending_discount_code = None
            user.pending_discount_percent = None
            promo_row = await session.scalar(select(DiscountCode).where(DiscountCode.code == used_promo_code))
            if promo_row:
                promo_row.current_uses += 1
            session.add(UsedDiscount(user_id=user_id, code=used_promo_code))

        # ---- اول انبار رو چک کن ----
        stock = await session.scalar(
            select(StockConfig).where(
                StockConfig.service_id == plan.id,
                StockConfig.status == "AVAILABLE",
            )
        )

        order = None

        if stock:
            stock.status = "SOLD"
            stock.assigned_user = user_id
            stock.sold_at = datetime.now(timezone.utc)

            order = ServiceOrder(
                user_id=user_id, plan_id=plan.id, service_name=plan.name,
                config_name=stock.config_name or "-", config_link=stock.config_link,
                price=final_price, inventory_id=stock.id, panel_id=stock.panel_id,
                status="ACTIVE", expire_at=(None if plan.duration_days == 0 else datetime.now(timezone.utc) + timedelta(days=plan.duration_days)),
            )
            session.add(order)
            await session.commit()

        elif plan.delivery_mode == "MANUAL":
            order = ServiceOrder(
                user_id=user_id, plan_id=plan.id, service_name=plan.name,
                config_name="در حال آماده‌سازی", config_link="در حال آماده‌سازی",
                price=final_price, status="PENDING_MANUAL",
                expire_at=(None if plan.duration_days == 0 else datetime.now(timezone.utc) + timedelta(days=plan.duration_days)),
            )
            session.add(order)
            await session.commit()
            await message.answer(
                f"✅ سفارش شما ثبت شد!\n\n📦 سرویس: {plan_name}\n"
                f"⏳ سرویس شما به زودی توسط پشتیبانی به صورت دستی تحویل داده می‌شود.",
                reply_markup=await main_keyboard(),
            )
            await _notify_admins_manual_order(message.bot, order.id, plan.name, user_id, message.from_user.username, message.from_user.full_name)
            await state.clear()
            return

        else:  # AUTO - بساز از پنل
            panel = await session.get(Panel, plan.panel_id) if plan.panel_id else None

            processing_text = await get_content(session, "order_processing_text",
                                                 "⏳ سفارش شما در حال آماده‌سازیه، چند لحظه صبر کنید...")
            progress_msg = await message.answer(processing_text, reply_markup=await main_keyboard())

            config_link = None
            config_name = None
            build_error = None
            phantom_token = None

            if not panel:
                build_error = "این پلن به هیچ پنلی وصل نیست."
            else:
                volume_tag = volume_tag_from_plan(plan)
                config_name = await get_next_config_name(cat, volume_tag, panel)
                try:
                    config_link = await create_panel_account(panel, config_name, plan)
                    # اعتبارسنجی ساده: اگه چیزی که برگشته اصلاً شبیه لینک نبود، قبولش نکن
                    if not config_link or not str(config_link).startswith(("http://", "https://", "vless://",
                                                                            "vmess://", "trojan://", "ss://")):
                        raise ValueError(f"پاسخ پنل معتبر به نظر نمی‌رسه: {config_link!r}")

                    config_link, submerge_error = await apply_sub_merge(config_link, plan, config_name, user_id, panel=panel)
                    # اگه ادغام موفق بود، توکن PhantomHubs رو از URL استخراج می‌کنیم تا بعداً برای حذف داشته باشیم
                    phantom_token = config_link.split("/token/")[-1] if "/token/" in config_link else None
                    if submerge_error:
                        await _notify_admins(
                            message.bot,
                            f"⚠️ ادغام ساب برای سفارش کاربر {user_id} fail شد (لینک خام به‌جاش تحویل داده شد):\n\n{submerge_error}"
                        )
                except Exception as e:
                    build_error = str(e)
                    logger.error(f"خطا در ساخت کانفیگ از پنل «{panel.name if panel else '-'}»: {e}")

            try:
                await progress_msg.delete()
            except Exception:
                pass

            # اگه بعد از همه‌ی مراحل بالا (حتی بعد از ادغام ساب) بازم لینک خالی/نامعتبر بود،
            # این آخرین خط دفاعیه قبل از insert - جلوی کرش دیتابیس (NOT NULL constraint) رو می‌گیره
            if not build_error and (not config_link or not config_name):
                build_error = build_error or "بعد از ساخت کانفیگ، لینک یا نام کانفیگ خالی برگشت."

            if build_error:
                # ⚠️ کاربر هیچ جزئیات فنی/لینک پنل نمی‌بینه؛ سفارش به‌صورت PENDING_MANUAL ثبت میشه
                # تا خودت (ادمین) با /deliver_<id> لینک درست رو دستی بفرستی. مبلغ کسر شده باقی می‌مونه
                # چون سفارش لغو نشده، فقط تحویلش به‌صورت دستی تکمیل میشه.
                order = ServiceOrder(
                    user_id=user_id, plan_id=plan.id, service_name=plan.name,
                    config_name="در حال آماده‌سازی", config_link="در حال آماده‌سازی",
                    price=final_price, panel_id=panel.id if panel else None,
                    status="PENDING_MANUAL",
                    expire_at=(None if plan.duration_days == 0 else datetime.now(timezone.utc) + timedelta(days=plan.duration_days)),
                )
                session.add(order)
                await session.commit()

                await message.answer(
                    f"✅ سفارش شما ثبت شد!\n\n📦 سرویس: {plan_name}\n"
                    f"⏳ به دلیل یک مشکل فنی، سرویس شما طی چند دقیقه توسط پشتیبانی به صورت دستی تحویل داده می‌شود.",
                    reply_markup=await main_keyboard(),
                )
                await _notify_admins_manual_order(
                    message.bot, order.id, plan.name, user_id, message.from_user.username, message.from_user.full_name,
                    extra=f"⚠️ دلیل نیاز به تحویل دستی: {build_error}"
                )
                await state.clear()
                return

            order = ServiceOrder(
                user_id=user_id, plan_id=plan.id, service_name=plan.name,
                config_name=config_name, config_link=config_link,
                price=final_price, panel_id=panel.id, status="ACTIVE",
                phantom_token=phantom_token,
                expire_at=(None if plan.duration_days == 0 else datetime.now(timezone.utc) + timedelta(days=plan.duration_days)),
            )
            session.add(order)
            await session.commit()

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
                caption=caption,
                parse_mode="Markdown",
                reply_markup=await main_keyboard(),
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"ساخت QR کد برای سفارش {order.id} fail شد: {e}")
            await message.answer(caption, parse_mode="Markdown", reply_markup=await main_keyboard())

        await _notify_admins(
            message.bot,
            f"🛒 خرید جدید انجام شد\n\n"
            f"🆔 آیدی عددی: {user_id}\n"
            f"✏️ یوزرنیم: @{message.from_user.username if message.from_user.username else 'بدون یوزرنیم'}\n"
            f"👤 نام: {message.from_user.full_name}\n"
            f"سرویس: {plan_name}\nقیمت: {final_price:,} تومان\n"
            f"نام کانفیگ: {order.config_name}\nشماره سفارش: #{order.id}",
        )

    await state.clear()
