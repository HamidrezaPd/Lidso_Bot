# مهاجرت از SQLite به PostgreSQL

## چی توی کد عوض شد؟

1. `requirements.txt` → درایور `asyncpg` اضافه شد.
2. `database.py` → تابع `_add_column_if_missing` که قبلاً فقط با دستور مخصوص SQLite
   (`PRAGMA table_info`) کار می‌کرد، الان از ابزار عمومی SQLAlchemy استفاده می‌کنه و
   روی **هم SQLite هم PostgreSQL** درست کار می‌کنه.
3. یک اسکریپت جدید: `migrate_sqlite_to_postgres.py` برای انتقال یک‌باره‌ی داده‌های
   فعلیت (کاربرا، سرویس‌ها، تراکنش‌ها، تنظیمات و ...) از دیتابیس SQLite فعلی به Postgres.

خبر خوب: چون از اول پروژه از **SQLAlchemy async** استفاده کرده (نه دستورات خام SQLite)،
هیچ تغییری توی handlers، منطق پرداخت، پنل و بقیه‌ی کد لازم نبود. فقط با عوض کردن
`DATABASE_URL` توی `.env`، خودِ ربات به PostgreSQL وصل می‌شه.

---

## مرحله ۱: تست روی سیستم خودت (همینجا، قبل از خرید سرور)

### نصب PostgreSQL روی ویندوز/مک/لینوکس (سیستم شخصی)

**ویندوز:**
نصب‌کننده رو از https://www.postgresql.org/download/windows/ بگیر و نصب کن (یه پسورد
برای کاربر `postgres` ازت می‌خواد - یادت بمونه).

**مک (با Homebrew):**
```bash
brew install postgresql@16
brew services start postgresql@16
```

**لینوکس (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib -y
sudo systemctl start postgresql
```

### ساخت دیتابیس و کاربر تست

```bash
sudo -u postgres psql
```
(روی ویندوز/مک: فقط `psql -U postgres`)

توی psql:
```sql
CREATE DATABASE lidso_db;
CREATE USER lidso_user WITH PASSWORD 'test1234';
GRANT ALL PRIVILEGES ON DATABASE lidso_db TO lidso_user;
\q
```

### نصب پکیج‌های پایتون

```bash
pip install -r requirements.txt
```

### اجرای مهاجرت (اختیاری - اگه می‌خوای داده‌های فعلیت هم منتقل بشه)

اگه فقط می‌خوای تست کنی که ربات با Postgres راه می‌افته (بدون داده‌ی قبلی)، این
مرحله رو رد کن و برو مرحله‌ی بعد.

اگه می‌خوای داده‌های فعلی SQLite (کاربرا، تراکنش‌ها و ...) هم منتقل بشه:

```bash
python migrate_sqlite_to_postgres.py \
    --sqlite-path lidso_database.db \
    --postgres-url "postgresql+asyncpg://lidso_user:test1234@localhost:5432/lidso_db"
```

اگه موفق بود، در آخر تعداد رکوردهای منتقل‌شده رو برات چاپ می‌کنه.

### عوض کردن `.env`

فایل `.env` رو باز کن و خط `DATABASE_URL` رو عوض کن:

```
DATABASE_URL=postgresql+asyncpg://lidso_user:test1234@localhost:5432/lidso_db
```

### اجرای ربات

```bash
python main.py
```

اگه بدون خطا بالا اومد و توی لاگ چیزی شبیه به خطای اتصال دیتابیس ندیدی، یعنی
موفق وصل شده. برو توی بات چک کن پروفایل/کیف پول/خرید سرویس درست کار می‌کنن.

### برگشت به SQLite (اگه خواستی)

کافیه `DATABASE_URL` رو دوباره به همون مقدار قبلی برگردونی:
```
DATABASE_URL=sqlite+aiosqlite:///lidso_database.db
```
داده‌های SQLite دست‌نخورده باقی می‌مونن (اسکریپت مهاجرت فقط می‌خونه، پاک نمی‌کنه).

---

## مرحله ۲: وقتی سرور واقعی گرفتی

وقتی سرور لینوکس (پیشنهادم Ubuntu 22.04 LTS) رو گرفتی، به من بگو دقیقاً چه چیزی
داری (رم، مشخصات، دسترسی SSH) تا مرحله‌به‌مرحله راهنماییت کنم برای:
- نصب PostgreSQL روی سرور
- انتقال امن دیتابیس (از سیستم خودت یا از صفر)
- تنظیم ربات به‌عنوان سرویس دائمی (systemd) که با ریبوت سرور هم خودش بالا بیاد
- تنظیم فایروال و امنیت پایه‌ای سرور

فقط با تغییر `DATABASE_URL` و اجرای `migrate_sqlite_to_postgres.py` روی سرور
(یا انتقال فایل SQLite به سرور و اجرای اسکریپت اونجا)، کل کار تمومه.
