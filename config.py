import os
from dotenv import load_dotenv

load_dotenv()

# ===========================
# Telegram
# ===========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).split(",") if x.strip()
]

BOT_USERNAME = os.getenv("BOT_USERNAME", "")  # برای ساخت لینک رفرال - یوزرنیم ربات بدون @

# ===========================
# پروکسی (چون تلگرام در ایران فیلتره)
# اگه روی سیستم خودت یه VPN/V2Ray داری که پورت SOCKS5 یا HTTP لوکال میده
# آدرسشو اینجا بذار. اگه سرور خارج بود و نیازی نبود، این خط رو خالی بذار.
# ===========================
PROXY_URL = os.getenv("PROXY_URL") or None  # مثلا "http://127.0.0.1:10808"

# ===========================
# Database
# ===========================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///lidso_database.db"
)

# ===========================
# Project
# ===========================

DEBUG = True
LOG_LEVEL = "INFO"
TIMEZONE = "Asia/Tehran"

# ===========================
# Inventory (انبار)
# ===========================

AUTO_CREATE_IF_EMPTY = True
# اگر انبار خالی بود، ربات از پنل کانفیگ بسازد.

# ===========================
# Wallet
# ===========================

MIN_TOPUP_AMOUNT = int(os.getenv("MIN_TOPUP_AMOUNT", "10000"))

# ===========================
# Referral
# ===========================

REFERRAL_BONUS_AMOUNT = int(os.getenv("REFERRAL_BONUS_AMOUNT", "20000"))
REFERRAL_NEEDED_COUNT = int(os.getenv("REFERRAL_NEEDED_COUNT", "3"))

# ===========================
# تمدید / حذف خودکار
# ===========================

RENEWAL_GRACE_HOURS = int(os.getenv("RENEWAL_GRACE_HOURS", "72"))
# بعد از اتمام سرویس چند ساعت مهلت بدیم قبل از حذف خودکار از پنل

SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "30"))
# هر چند دقیقه یکبار ربات چک کنه سرویس‌های منقضی شده رو

# ===========================
# درگاه پرداخت (Zarinpal) - TODO: وقتی مرچنت کد گرفتی پر کن
# ===========================
ZARINPAL_MERCHANT_ID = os.getenv("ZARINPAL_MERCHANT_ID", "")
ZARINPAL_CALLBACK_URL = os.getenv("ZARINPAL_CALLBACK_URL", "")
