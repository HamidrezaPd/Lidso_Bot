r"""
اسکریپت مهاجرت داده از SQLite به PostgreSQL

این اسکریپت رو جدا از خودِ ربات اجرا می‌کنی - فقط یک بار، وقتی می‌خوای دیتابیس
قدیمی (SQLite) رو کامل با همه‌ی داده‌هاش به PostgreSQL منتقل کنی.

نحوه‌ی استفاده:
------------------------------------------------------------------
۱) اول مطمئن شو PostgreSQL نصب و روشنه و یک دیتابیس خالی براش ساختی:

    sudo -u postgres psql
    CREATE DATABASE lidso_db;
    CREATE USER lidso_user WITH PASSWORD 'یک-پسورد-قوی';
    GRANT ALL PRIVILEGES ON DATABASE lidso_db TO lidso_user;
    \q

۲) پکیج‌های لازم رو نصب کن (اگه از قبل نصب نکردی):

    pip install -r requirements.txt

۳) این اسکریپت رو اجرا کن و آدرس هر دو دیتابیس رو بهش بده:

    python migrate_sqlite_to_postgres.py \
        --sqlite-path lidso_database.db \
        --postgres-url "postgresql+asyncpg://lidso_user:پسورد@localhost:5432/lidso_db"

۴) بعد از اتمام موفق، فایل .env رو عوض کن:

    DATABASE_URL=postgresql+asyncpg://lidso_user:پسورد@localhost:5432/lidso_db

۵) ربات رو دوباره اجرا کن - از این به بعد رو PostgreSQL کار می‌کنه.

نکته: این اسکریپت idempotent نیست - یعنی اگه دوباره اجراش کنی روی یه دیتابیس Postgres
که از قبل داده داره، رکوردهای تکراری اضافه می‌کنه. همیشه روی یه دیتابیس Postgres کاملاً
خالی اجراش کن.
------------------------------------------------------------------
"""
import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, inspect as sa_inspect

from database import Base, User, Panel, Category, CategoryDuration, ServicePlan, StockConfig, \
    DiscountCode, UsedDiscount, ServiceOrder, AdminLog, BotContent, WalletTransaction, \
    MenuButton, CryptoConfig, PaymentGatewayConfig, SubMergeConfig, Referral

# ترتیب مهمه: جدول‌هایی که به جدول دیگه ارجاع منطقی دارن (نه FK رسمی، ولی برای خوانایی) بعدتر میان
ALL_MODELS = [
    User, Panel, Category, CategoryDuration, ServicePlan, StockConfig,
    DiscountCode, UsedDiscount, ServiceOrder, AdminLog, BotContent,
    WalletTransaction, MenuButton, CryptoConfig, PaymentGatewayConfig,
    SubMergeConfig, Referral,
]


async def migrate(sqlite_path: str, postgres_url: str, batch_size: int = 500):
    sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"

    print(f"🔗 اتصال به SQLite: {sqlite_url}")
    sqlite_engine = create_async_engine(sqlite_url, echo=False)
    sqlite_session = async_sessionmaker(sqlite_engine, expire_on_commit=False, class_=AsyncSession)

    print(f"🔗 اتصال به PostgreSQL: {postgres_url.split('@')[-1]}")
    pg_engine = create_async_engine(postgres_url, echo=False)
    pg_session = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

    # ساخت جدول‌ها روی Postgres (اگه از قبل نبودن)
    print("🛠  ساخت جدول‌ها روی PostgreSQL...")
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # چک کن جدول‌های مقصد واقعاً خالی‌ان (جلوگیری از دوبار اجرا روی دیتای واقعی)
    async with pg_session() as session:
        existing_user = await session.scalar(select(User).limit(1))
        if existing_user:
            print("⚠️  هشدار: دیتابیس PostgreSQL از قبل داده داره (حداقل یک کاربر پیدا شد).")
            answer = input("مطمئنی می‌خوای ادامه بدی؟ ممکنه رکورد تکراری ایجاد بشه. (yes/no): ")
            if answer.strip().lower() != "yes":
                print("❌ عملیات لغو شد.")
                return

    total_rows = 0
    for model in ALL_MODELS:
        table_name = model.__tablename__
        async with sqlite_session() as s_session:
            rows = (await s_session.execute(select(model))).scalars().all()

        if not rows:
            print(f"  ⏭  {table_name}: خالیه، رد شد")
            continue

        cols = [c.key for c in sa_inspect(model).columns if c.key != "id"]
        # id رو دست‌نخورده منتقل می‌کنیم تا رفرنس‌های دستی (مثل service_plans.id که جای دیگه استفاده شده) بشکنه نه

        async with pg_session() as p_session:
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                for row in batch:
                    data = {c: getattr(row, c) for c in cols}
                    data["id"] = row.id  # id اصلی رو حفظ می‌کنیم
                    p_session.add(model(**data))
                await p_session.commit()

        print(f"  ✅ {table_name}: {len(rows)} رکورد منتقل شد")
        total_rows += len(rows)

    # بعد از انتقال دستیِ id ها، sequence شمارنده‌ی Postgres (SERIAL) رو باید sync کنیم
    # وگرنه رکورد بعدی که با INSERT عادی (بدون id) اضافه بشه، ممکنه با id تکراری تصادم کنه
    print("🔧 هماهنگ‌سازی شمارنده‌ی id (sequence) با آخرین id هر جدول...")
    async with pg_engine.begin() as conn:
        for model in ALL_MODELS:
            table_name = model.__tablename__
            await conn.exec_driver_sql(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
                f"(SELECT MAX(id) FROM {table_name}) IS NOT NULL)"
            )

    await sqlite_engine.dispose()
    await pg_engine.dispose()

    print(f"\n🎉 مهاجرت با موفقیت تموم شد! مجموعاً {total_rows} رکورد منتقل شد.")
    print("حالا فایل .env رو با آدرس PostgreSQL آپدیت کن و ربات رو دوباره اجرا کن.")


def main():
    parser = argparse.ArgumentParser(description="مهاجرت داده از SQLite به PostgreSQL")
    parser.add_argument("--sqlite-path", default="lidso_database.db", help="مسیر فایل دیتابیس SQLite فعلی")
    parser.add_argument("--postgres-url", required=True,
                         help="آدرس کامل PostgreSQL - مثلا postgresql+asyncpg://user:pass@localhost:5432/dbname")
    args = parser.parse_args()

    if not args.postgres_url.startswith("postgresql+asyncpg://"):
        print("❌ آدرس PostgreSQL باید با postgresql+asyncpg:// شروع بشه (نه postgresql:// ساده).")
        sys.exit(1)

    asyncio.run(migrate(args.sqlite_path, args.postgres_url))


if __name__ == "__main__":
    main()
