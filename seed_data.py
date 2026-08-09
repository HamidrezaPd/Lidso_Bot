"""
موقع اولین اجرا، اگه دیتابیس خالی بود، پلن‌ها و متن‌های پیش‌فرض رو می‌سازه
(دقیقا طبق چیزی که تعریف کردی). این مقادیر بعدا از بات ادمین قابل تغییرن.
"""
from sqlalchemy import select
from database import async_session, ServicePlan, BotContent, Category, MenuButton, CategoryDuration


def _duration_label(days: int) -> str:
    if days == 0:
        return "نامحدود ♾"
    if days % 30 == 0:
        months = days // 30
        return "یک‌ماهه" if months == 1 else f"{months} ماهه"
    return f"{days} روزه"


async def _migrate_category_durations(session):
    """
    مهاجرت امن: هر پلن قدیمی که duration_id نداره رو به یه CategoryDuration مناسب
    (بر اساس همون duration_days که از قبل داشته) وصل می‌کنه - رفتار فعلی دست‌نخورده می‌مونه
    ولی از این به بعد سیستم مدت‌زمان کاملاً داینامیک میشه.
    """
    plans = (await session.execute(select(ServicePlan).where(ServicePlan.duration_id.is_(None)))).scalars().all()
    for plan in plans:
        cat = await session.scalar(select(Category).where(Category.prefix == plan.category))
        if not cat:
            continue
        existing = await session.scalar(
            select(CategoryDuration).where(
                CategoryDuration.category_id == cat.id,
                CategoryDuration.days == plan.duration_days,
            )
        )
        if not existing:
            existing = CategoryDuration(
                category_id=cat.id, label=_duration_label(plan.duration_days),
                days=plan.duration_days, sort_order=0,
            )
            session.add(existing)
            await session.flush()
        plan.duration_id = existing.id
    if plans:
        await session.commit()


async def seed_defaults():
    async with async_session() as session:
        existing_menu = await session.scalar(select(MenuButton).limit(1))
        if not existing_menu:
            keys = [
                "btn_buy", "btn_tariffs", "btn_free_trial", "btn_my_services", "btn_wallet",
                "btn_profile", "btn_invite", "btn_guide", "btn_support",
            ]
            session.add_all([
                MenuButton(key=k, is_custom=False, sort_order=i, enabled=True,
                           full_width=(k == "btn_free_trial"))
                for i, k in enumerate(keys)
            ])

        existing_cat = await session.scalar(select(Category).limit(1))
        if not existing_cat:
            session.add_all([
                Category(title="Lidso Prime | لیدسو پرایم", prefix="LidsoPrime", sort_order=1),
                Category(title="Lidso Unlimited | لیدسو آنلیمیتد", prefix="LidsoUnlimited", sort_order=2),
            ])
            await session.flush()

        existing = await session.scalar(select(ServicePlan).limit(1))
        if not existing:
            prime_cat = await session.scalar(select(Category).where(Category.prefix == "LidsoPrime"))
            unl_cat = await session.scalar(select(Category).where(Category.prefix == "LidsoUnlimited"))

            prime_dur = CategoryDuration(category_id=prime_cat.id, label="یک‌ماهه", days=30, sort_order=1)
            unl_dur = CategoryDuration(category_id=unl_cat.id, label="یک‌ماهه", days=30, sort_order=1)
            session.add_all([prime_dur, unl_dur])
            await session.flush()

            plans = [
                # Prime (حجمی) - AUTO یعنی اگه انبار خالی بود از پنل بساز
                ServicePlan(category="LidsoPrime", name="10 گیگ پرایم", volume_gb=10, duration_id=prime_dur.id,
                            price=30000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=1),
                ServicePlan(category="LidsoPrime", name="20 گیگ پرایم", volume_gb=20, duration_id=prime_dur.id,
                            price=60000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=2),
                ServicePlan(category="LidsoPrime", name="30 گیگ پرایم", volume_gb=30, duration_id=prime_dur.id,
                            price=90000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=3),
                ServicePlan(category="LidsoPrime", name="50 گیگ پرایم", volume_gb=50, duration_id=prime_dur.id,
                            price=150000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=4),
                ServicePlan(category="LidsoPrime", name="100 گیگ پرایم", volume_gb=100, duration_id=prime_dur.id,
                            price=250000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=5),
                # Unlimited
                ServicePlan(category="LidsoUnlimited", name="1 کاربره", volume_gb=0, duration_id=unl_dur.id,
                            price=179000, duration_days=30, delivery_mode="AUTO", max_users=1, sort_order=1),
                ServicePlan(category="LidsoUnlimited", name="2 کاربره", volume_gb=0, duration_id=unl_dur.id,
                            price=239000, duration_days=30, delivery_mode="AUTO", max_users=2, sort_order=2),
            ]
            session.add_all(plans)

        existing_content = await session.scalar(select(BotContent).limit(1))
        if not existing_content:
            defaults = {
                "tariffs": "💰 تعرفه‌های Lidso\n\nبرای مشاهده و خرید از دکمه «خرید سرویس» استفاده کنید.\n(این متن از بات ادمین قابل ویرایشه)",
                "guide": "📚 آموزش اتصال\n\nبه زودی آموزش کامل قرار می‌گیرد.\n(این متن از بات ادمین قابل ویرایشه)",
                "support_id": "@your_support_username",
                "card_number": "6037-XXXX-XXXX-XXXX",
                "card_holder": "نام صاحب حساب",
                "crypto_address": "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX (TRC20)",
                "welcome": "👋 به ربات فروش Lidso خوش اومدی!\nاز منوی پایین یکی از گزینه‌ها رو انتخاب کن.",
                "order_processing_text": "⏳ سفارش شما در حال آماده‌سازیه، چند لحظه صبر کنید...",
                "min_topup_amount": "50000",
                "min_topup_card": "50000",
                "max_topup_card": "0",
                "min_topup_gateway": "50000",
                "max_topup_gateway": "0",
                "min_topup_crypto": "50000",
                "max_topup_crypto": "0",
            }
            session.add_all([BotContent(key=k, value=v) for k, v in defaults.items()])

        await session.commit()

        # مهاجرت پلن‌های قدیمی به سیستم مدت‌زمان داینامیک
        await _migrate_category_durations(session)
