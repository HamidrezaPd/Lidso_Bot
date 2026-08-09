"""
اتصال به PhantomHubs Subscription Panel - بر اساس مستندات رسمی API که دریافت شد.

مستندات:
- Base URL: https://api.phantomhubs.shop
- Auth: هدر Authorization: Bearer <PANEL_SYNC_TOKEN>
- POST /internal/configs  → ثبت/آپدیت یک ساب (خروجی موفق فقط رشته‌ی "ok" هست، نه JSON)
- لینک نهایی مشتری: {base_url}/token/{token}  (خودمون می‌سازیمش، سرور برنمی‌گردونه)
"""
import secrets
import logging
import httpx
from sqlalchemy import select
from database import async_session, SubMergeConfig

logger = logging.getLogger(__name__)


async def get_submerge_config():
    async with async_session() as session:
        return await session.scalar(select(SubMergeConfig).limit(1))


def _device_limit_for_plan(plan) -> int:
    """طبق دستورالعمل: برای سرویس‌های حجمی (Prime) صفر (نامحدود)،
    برای سرویس‌های کاربر-محور (Unlimited) به تعداد کاربر پلن.
    اگه plan این فیلدها رو نداشت (مثلاً TrialPlan)، پیش‌فرض 0 (نامحدود) در نظر گرفته میشه."""
    category = getattr(plan, "category", None)
    is_user_based = category == "LidsoUnlimited" or getattr(plan, "volume_gb", 0) == 0
    return getattr(plan, "max_users", 0) if is_user_based else 0


def _generate_token(config_name: str) -> str:
    """توکن عمومی یکتا - بر پایه‌ی اسم کانفیگ + یه بخش تصادفی برای امنیت"""
    safe_name = "".join(c for c in config_name.lower() if c.isalnum() or c == "_")
    return f"{safe_name}_{secrets.token_urlsafe(6)}"


async def delete_from_phantom(phantom_token: str) -> str | None:
    """
    حذف واقعی و کامل رکورد از PhantomHubs با DELETE /internal/configs/{token}.
    این endpoint به توکن داخلی کامل (PANEL_SYNC_TOKEN / PANEL_EXTRA_SYNC_TOKENS) نیاز داره -
    توکن محدود INTEGRATION_SYNC_TOKEN قدیمی روش کار نمی‌کرد، برای همین قبلاً به‌جاش
    upstream_url رو باطل می‌کردیم. الان که توکن کامل داریم، حذف واقعی انجام میشه.

    خروجی: None یعنی موفق، رشته یعنی متن خطا.
    """
    if not phantom_token:
        return None

    cfg = await get_submerge_config()
    if not cfg or not cfg.base_url:
        return None

    headers = {"Content-Type": "application/json"}
    if cfg.sync_token:
        headers["Authorization"] = f"Bearer {cfg.sync_token}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{cfg.base_url.rstrip('/')}/internal/configs/{phantom_token}",
                headers=headers,
            )
            # اگه توکن از قبل روی سرور وجود نداشت (404) عملاً همون چیزیه که می‌خواستیم (دیگه نیست) - خطا حساب نمیشه
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return f"HTTP {e.response.status_code}: {body}"
    except Exception as e:
        return str(e)


async def apply_sub_merge(raw_link: str, plan, config_name: str, telegram_user_id: int = None, panel=None):
    """
    خروجی: (لینک نهایی که باید به مشتری داده بشه, متن خطا یا None)
    - غیرفعال بود: (raw_link, None)
    - موفق بود: (لینک PhantomHubs, None)
    - fail شد: (raw_link, "متن دقیق خطا") - یعنی بازم لینک قابل‌استفاده برمی‌گرده، فقط
      باید caller خطا رو به ادمین اطلاع بده.

    panel: اگه داده بشه، علاوه بر تنظیمات global ادغام ساب، فلگ اختصاصی همون پنل هم چک میشه -
    یعنی هر دو باید فعال باشن (global AND پنل) تا واقعاً ادغام ساب اعمال بشه.
    """
    cfg = await get_submerge_config()
    if not cfg or not cfg.active or not cfg.base_url:
        return raw_link, None
    if panel is not None and not getattr(panel, "submerge_enabled", False):
        return raw_link, None

    token = _generate_token(config_name)

    # ⚠️ طبق مستندات PhantomHubs، volume_gb باید عدد صحیح (integer) باشه، نه اعشاری - وگرنه
    # سرور با خطای 422 رد می‌کنه. این فیلد فقط برای نمایش روی صفحه‌ی PhantomHubs استفاده میشه
    # (محدودیت واقعی حجم رو خودِ پنل اصلی - مرزبان/پاسارگارد - اعمال می‌کنه، نه اینجا).
    # مقدار 0 توی این API یعنی "نامحدود"، پس برای پلن‌های واقعاً کم‌حجم (مثل تست 0.1 گیگ)
    # باید حداقل 1 فرستاده بشه تا اشتباهاً نامحدود نشون داده نشه.
    raw_volume = plan.volume_gb or 0
    if raw_volume == 0:
        volume_gb_int = 0  # پلن واقعاً نامحدوده
    else:
        volume_gb_int = max(1, round(raw_volume))

    payload = {
        "token": token,
        "upstream_url": raw_link,
        "volume_gb": volume_gb_int,
        "category_key": cfg.category or "default",
        "is_sold": True,
        "service_name": config_name,  # همون اسمی که توی پنل گذاشته شده
        "panel_username": config_name,
        "profile_title": cfg.display_name or "",
        "device_limit": _device_limit_for_plan(plan),
        "channel_handle": cfg.support_channel or "",
        "show_header": bool(cfg.show_site_header),
        "show_config_preview": bool(cfg.show_sub_configs),
        "info_proxies_enabled": bool(cfg.add_info_configs),
    }
    if telegram_user_id:
        payload["telegram_user_id"] = telegram_user_id

    # اگه توکن تنظیم شده باشه هدر Authorization می‌فرستیم، اگه نباشه بدون هدر امتحان می‌کنیم
    # (ممکنه سرور خودش توکن پیش‌فرض داشته باشه یا اصلاً نیاز به احراز هویت نداشته باشه)
    headers = {"Content-Type": "application/json"}
    if cfg.sync_token:
        headers["Authorization"] = f"Bearer {cfg.sync_token}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.info(f"📤 ارسال به PhantomHubs برای {config_name}: profile_title={payload['profile_title']!r}, "
                        f"channel_handle={payload['channel_handle']!r}, token={token!r}")
            resp = await client.post(
                f"{cfg.base_url.rstrip('/')}/internal/configs",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            public_url = f"{cfg.base_url.rstrip('/')}/token/{token}"
            logger.info(f"✅ PhantomHubs پاسخ داد: {resp.text[:200]!r}")
            return public_url, None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return raw_link, f"HTTP {e.response.status_code if e.response is not None else '?'}: {body}"
    except Exception as e:
        return raw_link, str(e)
