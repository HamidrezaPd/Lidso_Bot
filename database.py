from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, BigInteger, Float, Boolean, DateTime, select
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    balance = Column(Integer, default=0)
    referred_by = Column(BigInteger, nullable=True)
    referral_count = Column(Integer, default=0)
    total_purchases = Column(Integer, default=0)
    total_volume = Column(Integer, default=0)
    total_unlimited_purchases = Column(Integer, default=0)
    # تعداد خریدهای حجم نامحدود (پلن‌هایی که volume_gb=0 دارن) - جدا از حجم گیگابایتی
    total_spent = Column(Float, default=0.0)
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    used_free_trial = Column(Boolean, default=False)
    # آیا این کاربر قبلاً یه‌بار از تست رایگان استفاده کرده. ادمین می‌تونه از توی بات، این فلگ
    # رو برای همه‌ی کاربرا ریست کنه (وقتی می‌خواد دوباره تست رو در دسترس همه بذاره).

    # کد تخفیفی که کاربر وارد کرده و هنوز مصرف نشده (روی خرید بعدی اعمال میشه)
    pending_discount_code = Column(String, nullable=True)
    pending_discount_percent = Column(Integer, nullable=True)


class Panel(Base):
    __tablename__ = "panels"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    # مثلا: Prime Panel
    panel_type = Column(String, nullable=False)
    # marzban | pasarguard | youpanel
    url = Column(String, nullable=False)
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    api_token = Column(String, nullable=True)
    group_ids = Column(String, default="1")
    # آیدی گروه‌های پنل که موقع ساخت کاربر انتخاب میشن، با کاما جدا (مثلا "1" یا "1,2,3")
    # ⚠️ این فیلد فقط برای پنل‌هایی که از "group_ids" استفاده می‌کنن معتبره (مثلا پاسارگارد).
    protocol = Column(String, default="vless")
    # پروتکل اصلی برای ساخت کاربر (vless / vmess / trojan / shadowsocks) - برای پنل‌هایی مثل
    # Marzban استاندارد (MMD) که به‌جای group_ids از proxies/inbounds استفاده می‌کنن لازمه.
    inbound_tags = Column(String, nullable=True)
    # تگ اینباند(های) همون پروتکل، با کاما جدا (مثلا "VLESS TCP" یا چند تا با کاما). فقط برای
    # پنل‌هایی که به روش proxies/inbounds کار می‌کنن (مثل Marzban استاندارد/MMD) لازمه.
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    submerge_enabled = Column(Boolean, default=False)
    # آیا سرویس‌هایی که با این پنل خاص ساخته میشن باید از ادغام ساب (PhantomHubs) عبور کنن.
    # مستقل از تنظیمات global ادغام ساب - هر دو باید فعال باشن تا واقعاً اعمال بشه.


class Category(Base):
    """دسته‌بندی سرویس‌ها (Prime / Unlimited / VIP / ...) - کاملاً از بات ادمین قابل افزودنه"""
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)     # متن دکمه، مثلا "Lidso VIP | لیدسو وی‌آی‌پی"
    prefix = Column(String, unique=True, nullable=False)  # مثلا LidsoVIP - برای اسم‌گذاری کانفیگ و ServicePlan.category
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    icon_custom_emoji_id = Column(String, nullable=True)
    # آیدی ایموجی پرمیوم کنار متن دکمه‌ی این دسته‌بندی (Bot API 9.4+)
    style = Column(String, nullable=True)
    # رنگ دکمه: primary (آبی) / success (سبز) / danger (قرمز) / خالی = پیش‌فرض


class CategoryDuration(Base):
    """مدت‌زمان‌های قابل‌تعریف برای هر دسته‌بندی (مثلا یک‌ماهه/دوماهه/نامحدود) - کاملاً از ادمین قابل مدیریته"""
    __tablename__ = "category_durations"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, nullable=False)  # Category.id
    label = Column(String, nullable=False)  # متن دکمه، مثلا "یک ماهه" یا "نامحدود"
    days = Column(Integer, default=30)  # 0 = نامحدود
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)


class ServicePlan(Base):
    __tablename__ = 'service_plans'

    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False)   # LidsoPrime / LidsoUnlimited
    name = Column(String, nullable=False)         # "10 گیگ پرایم" یا "1 کاربره"
    volume_gb = Column(Integer, default=0)        # برای Unlimited برابر 0
    price = Column(Integer, nullable=False)
    panel_id = Column(Integer, nullable=True)
    duration_id = Column(Integer, nullable=True)  # CategoryDuration.id

    duration_days = Column(Integer, default=30)
    delivery_mode = Column(String, default="AUTO")
    # AUTO (بساز از پنل) | MANUAL (تحویل دستی توسط ادمین) | هردو با اولویت انبار
    max_users = Column(Integer, default=1)
    hwid_limit = Column(Integer, default=0)
    # محدودیت تعداد دستگاه (HWID). 0 = دست نزن/ارسال نکن، فقط برای پنل‌هایی که این فیلد رو دارن معنی داره
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    icon_custom_emoji_id = Column(String, nullable=True)
    # آیدی ایموجی پرمیوم کنار متن دکمه‌ی این پلن (Bot API 9.4+)
    style = Column(String, nullable=True)
    # رنگ دکمه: primary (آبی) / success (سبز) / danger (قرمز) / خالی = پیش‌فرض


class TrialPlan(Base):
    """
    سرویس‌های تست رایگان - کاملاً جدا از ServicePlan (پلن‌های پولی) نگه داشته میشه چون منطق
    متفاوتی داره (هر کاربر فقط یه‌بار، بدون قیمت، حجم اعشاری مثل 0.1 گیگ مجازه).
    """
    __tablename__ = "trial_plans"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)          # مثلا "تست 100 مگ لیدسو"
    prefix = Column(String, nullable=False, default="LidsoTest")
    # پیشوند نام‌گذاری کانفیگ - مثلا LidsoTest میشه LidsoTest_100mb_483
    volume_gb = Column(Float, default=0.1)          # حجم به گیگ (0.1 = 100 مگابایت)
    duration_days = Column(Integer, default=1)
    panel_id = Column(Integer, nullable=True)
    hwid_limit = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class StockConfig(Base):
    """انبار کانفیگ‌های از قبل ساخته شده"""
    __tablename__ = "stock_configs"
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, nullable=False)   # ServicePlan.id
    panel_id = Column(Integer, nullable=True)
    config_name = Column(String, unique=True, nullable=True)
    config_link = Column(String, nullable=False)
    source = Column(String, default="MANUAL")
    # MANUAL | AUTO
    status = Column(String, default="AVAILABLE")
    # AVAILABLE | SOLD
    assigned_user = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    sold_at = Column(DateTime, nullable=True)


class DiscountCode(Base):
    __tablename__ = 'discount_codes'

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    percent = Column(Integer, nullable=False)
    max_uses = Column(Integer, default=10)
    current_uses = Column(Integer, default=0)
    active = Column(Boolean, default=True)


class UsedDiscount(Base):
    __tablename__ = 'used_discounts'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    code = Column(String, nullable=False)
    used_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ServiceOrder(Base):
    __tablename__ = 'service_orders'

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    plan_id = Column(Integer, nullable=True)
    service_name = Column(String, nullable=False)
    config_name = Column(String, nullable=False)
    config_link = Column(String, nullable=False)
    price = Column(Integer, default=0)

    inventory_id = Column(Integer, nullable=True)
    panel_id = Column(Integer, nullable=True)

    status = Column(String, default="ACTIVE")
    # ACTIVE | PENDING_MANUAL | EXPIRED | REMOVED

    is_trial = Column(Boolean, default=False)
    # اگه True باشه یعنی این سفارش از تست رایگانه - plan_id توی این حالت به TrialPlan.id
    # اشاره می‌کنه نه ServicePlan.id (چون این دو تا مدل کاملاً جدان)

    phantom_token = Column(String, nullable=True)
    # توکنی که برای این سرویس توی PhantomHubs ثبت شد - برای حذف بعدی نیاز داریم

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expire_at = Column(DateTime, nullable=True)
    renew_notified = Column(Boolean, default=False)
    renew_notified_at = Column(DateTime, nullable=True)


class AdminLog(Base):
    __tablename__ = 'admin_logs'

    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class BotContent(Base):
    """جدول متون و تنظیمات قابل ویرایش از بات ادمین"""
    __tablename__ = 'bot_contents'

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    # tariffs, guide, support_id, card_number, card_holder, crypto_address, welcome, required_channel
    value = Column(String, nullable=False)
    entities = Column(String, nullable=True)
    # JSON سریالایز شده‌ی entities تلگرام (برای حفظ ایموجی پرمیوم/بولد/لینک و ...) - فقط برای متن پیام‌ها معنی داره، نه دکمه‌ها
    icon_custom_emoji_id = Column(String, nullable=True)
    # آیدی ایموجی پرمیوم کنار متن دکمه (فقط وقتی این کلید مربوط به یه دکمه باشه، نه متن پیام)
    style = Column(String, nullable=True)
    # رنگ دکمه: primary (آبی) / success (سبز) / danger (قرمز) / خالی = پیش‌فرض


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    amount = Column(Integer, nullable=False)

    transaction_type = Column(String, nullable=False)
    # DEPOSIT | PURCHASE | REFUND | BONUS | RENEWAL

    method = Column(String, nullable=True)
    # CARD | CRYPTO | GATEWAY | WALLET | ADMIN

    status = Column(String, default="SUCCESS")
    # SUCCESS | PENDING | REJECTED | FAILED

    receipt_file_id = Column(String, nullable=True)
    description = Column(String, nullable=True)
    handled_by = Column(BigInteger, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    # مهلت پرداخت (برای AWAITING_RECEIPT) - بعد از این زمان دیگه معتبر نیست
    gateway_invoice_uid = Column(String, nullable=True)
    # آیدی فاکتور HooshPay - برای چک کردن وضعیت پرداخت
    crypto_amount = Column(Float, nullable=True)   # مقدار ارز دیجیتال محاسبه‌شده (تون/USDC)
    crypto_comment = Column(String, nullable=True)  # کامنت اختصاصی تراکنش (فقط برای TON)
    expire_notified = Column(Boolean, default=False)
    # آیا پیام «مهلت پرداخت تموم شد» برای این تراکنش قبلاً ارسال شده (جلوگیری از ارسال تکراری)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MenuButton(Base):
    """دکمه‌های منوی اصلی - هم دکمه‌های ثابت (با key) هم دکمه‌های سفارشی که ادمین اضافه می‌کنه"""
    __tablename__ = "menu_buttons"

    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=True)          # برای دکمه‌های ثابت: btn_buy, btn_wallet, ...
    is_custom = Column(Boolean, default=False)
    label = Column(String, nullable=True)         # فقط برای دکمه‌های سفارشی؛ متن مستقیم دکمه
    response_text = Column(String, nullable=True)  # فقط برای دکمه‌های سفارشی؛ پیامی که با کلیک نشون داده میشه
    sort_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    icon_custom_emoji_id = Column(String, nullable=True)
    # آیدی ایموجی پرمیوم که کنار متن دکمه نشون داده میشه (Bot API 9.4+)
    style = Column(String, nullable=True)
    # رنگ دکمه: primary (آبی) / success (سبز) / danger (قرمز) / خالی = پیش‌فرض اپ (Bot API 9.4+)
    full_width = Column(Boolean, default=False)
    # اگه True باشه، این دکمه تنها توی سطر خودش قرار می‌گیره (عرض ۱۰۰%)، حتی اگه چیدمان منو ۲ ستونی باشه


class CryptoConfig(Base):
    """تنظیمات پرداخت ارزی: گرام (تون) و USDC (BEP20)"""
    __tablename__ = "crypto_config"

    id = Column(Integer, primary_key=True)
    ton_address = Column(String, nullable=True)     # آدرس Tonkeeper برای گرام/تون
    bsc_address = Column(String, nullable=True)      # آدرس Trust Wallet برای USDC (BEP20)
    ton_api_key = Column(String, nullable=True)      # از @tonapibot می‌گیره (رایگان)
    bscscan_api_key = Column(String, nullable=True)  # از bscscan.com می‌گیره (رایگان)
    comment_enabled = Column(Boolean, default=False)
    # فقط برای گرام/تون معنی داره (BEP20 اصلاً از کامنت پشتیبانی نمی‌کنه)
    comment_prompt = Column(String, default="کامنت تراکنش")


class PaymentGatewayConfig(Base):
    """تنظیمات درگاه پرداخت HooshPay"""
    __tablename__ = "payment_gateway_config"

    id = Column(Integer, primary_key=True)
    base_url = Column(String, default="https://hooshpay.xyz")
    api_key = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    fee_mode = Column(String, default="split")  # seller | buyer | split
    active = Column(Boolean, default=False)


class SubMergeConfig(Base):
    """
    تنظیمات ابزار ادغام/بازطراحی ساب (مثلا phantomhubs.shop). وقتی active باشه، لینک خامی که
    از پنل وی‌پی‌ان می‌گیریم رو قبل از تحویل به مشتری، از این سرویس رد می‌کنیم تا لینک
    برندشده‌ی نهایی (با نام/آیکون/کانال پشتیبانی دلخواه) ساخته بشه.
    """
    __tablename__ = "sub_merge_config"

    id = Column(Integer, primary_key=True)
    base_url = Column(String, nullable=True)          # مثلا https://api.phantomhubs.shop
    sync_token = Column(String, nullable=True)         # PANEL_SYNC_TOKEN واقعی (Bearer token)
    admin_username = Column(String, nullable=True)     # دیگه استفاده نمیشه (روش قدیمی اشتباه)
    admin_password = Column(String, nullable=True)     # دیگه استفاده نمیشه (روش قدیمی اشتباه)
    display_name = Column(String, default="@LidsoNet")
    support_channel = Column(String, default="@LidsoNet")
    rewrite_rule = Column(String, nullable=True)       # متن باکس «بازنویسی آدرس کانفیگ»
    category = Column(String, default="manual")
    show_site_header = Column(Boolean, default=False)
    show_sub_configs = Column(Boolean, default=True)
    add_info_configs = Column(Boolean, default=False)
    active = Column(Boolean, default=False)


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True)
    inviter_id = Column(BigInteger, nullable=False)
    invited_id = Column(BigInteger, nullable=False)
    reward_given = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


async def _add_column_if_missing(conn, table: str, column: str, coltype: str = "TEXT"):
    """
    مهاجرت سبک و امن: اگه ستون از قبل نبود اضافه‌اش می‌کنه، بدون اینکه به داده‌های موجود دست بزنه.
    هم روی SQLite هم روی PostgreSQL کار می‌کنه (از SQLAlchemy inspector استفاده می‌کنه، نه PRAGMA خام).
    """
    from sqlalchemy import inspect as sa_inspect

    def _get_cols(sync_conn):
        return [c["name"] for c in sa_inspect(sync_conn).get_columns(table)]

    existing_cols = await conn.run_sync(_get_cols)
    if column not in existing_cols:
        # coltype ممکنه شامل DEFAULT هم باشه (مثلا "INTEGER DEFAULT 0") - این روی هر دو دیتابیس معتبره،
        # فقط مقدار BOOLEAN رو باید دیالکت-محور بدیم چون SQLite با 0/1 و Postgres با TRUE/FALSE کار می‌کنه
        is_postgres = conn.dialect.name == "postgresql"
        if is_postgres:
            coltype = (
                coltype
                .replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
                .replace("BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE")
            )
        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # ستون‌های جدیدی که به جدول‌های از قبل موجود اضافه شدن، اینجا با migration امن ست میشن
        await _add_column_if_missing(conn, "bot_contents", "entities", "TEXT")
        await _add_column_if_missing(conn, "service_plans", "hwid_limit", "INTEGER DEFAULT 0")
        await _add_column_if_missing(conn, "wallet_transactions", "expires_at", "TEXT")
        await _add_column_if_missing(conn, "menu_buttons", "icon_custom_emoji_id", "TEXT")
        await _add_column_if_missing(conn, "menu_buttons", "style", "TEXT")
        await _add_column_if_missing(conn, "menu_buttons", "full_width", "BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "bot_contents", "icon_custom_emoji_id", "TEXT")
        await _add_column_if_missing(conn, "bot_contents", "style", "TEXT")
        await _add_column_if_missing(conn, "categories", "icon_custom_emoji_id", "TEXT")
        await _add_column_if_missing(conn, "categories", "style", "TEXT")
        await _add_column_if_missing(conn, "service_plans", "icon_custom_emoji_id", "TEXT")
        await _add_column_if_missing(conn, "service_plans", "style", "TEXT")
        await _add_column_if_missing(conn, "panels", "group_ids", "TEXT DEFAULT '1'")
        await _add_column_if_missing(conn, "panels", "protocol", "TEXT DEFAULT 'vless'")
        await _add_column_if_missing(conn, "panels", "inbound_tags", "TEXT")
        await _add_column_if_missing(conn, "panels", "submerge_enabled", "BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "sub_merge_config", "sync_token", "TEXT")
        await _add_column_if_missing(conn, "service_orders", "phantom_token", "TEXT")
        await _add_column_if_missing(conn, "service_orders", "is_trial", "BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "service_plans", "duration_id", "INTEGER")
        await _add_column_if_missing(conn, "wallet_transactions", "gateway_invoice_uid", "TEXT")
        await _add_column_if_missing(conn, "wallet_transactions", "crypto_amount", "REAL")
        await _add_column_if_missing(conn, "wallet_transactions", "crypto_comment", "TEXT")
        await _add_column_if_missing(conn, "wallet_transactions", "expire_notified", "BOOLEAN DEFAULT 0")
        await _add_column_if_missing(conn, "users", "total_unlimited_purchases", "INTEGER DEFAULT 0")
        await _add_column_if_missing(conn, "users", "used_free_trial", "BOOLEAN DEFAULT 0")

    # اضافه کردن دکمه‌ی «تست رایگان» به منوی ربات‌هایی که از قبل نصب/راه‌اندازی شدن (نه فقط نصب‌های جدید)
    async with async_session() as session:
        has_trial_btn = await session.scalar(select(MenuButton).where(MenuButton.key == "btn_free_trial"))
        if not has_trial_btn:
            # اگه دکمه‌ای با sort_order=2 از قبل هست، همه رو یکی می‌کشیم عقب تا این دقیقاً ردیف دوم بشینه
            existing_buttons = (await session.execute(
                select(MenuButton).where(MenuButton.is_custom == False).order_by(MenuButton.sort_order)
            )).scalars().all()
            for b in existing_buttons:
                if b.sort_order >= 2:
                    b.sort_order += 1
            session.add(MenuButton(key="btn_free_trial", is_custom=False, sort_order=2,
                                    enabled=True, full_width=True))
            await session.commit()
