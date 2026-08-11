"""
اتصال به درگاه پرداخت HooshPay - بر اساس مستندات رسمی https://hooshpay.xyz/developers

روش کارکرد: چون بات فعلاً روی سیستم شخصی/بدون دامنه‌ی عمومی اجرا میشه، به‌جای webhook
(که نیاز به آدرس عمومی داره)، از polling (چک کردن دوره‌ای وضعیت فاکتور) استفاده می‌کنیم.
وقتی روی سرور واقعی با دامنه مستقر شد، می‌شه webhook هم اضافه کرد.

⚠️ نکته: مستندات HooshPay از {{BASE}} به‌عنوان placeholder استفاده کرده و هیچ‌جا صریح نگفته
دقیقاً چیه. فرض پیش‌فرض این کد https://hooshpay.xyz هست. اگه بعد از تست دیدی هنوز خطا میده،
از /admin → تنظیمات درگاه پرداخت، آدرس پایه رو با دامنه‌ی درست (که از پشتیبانی هوش‌پی می‌گیری) عوض کن.
"""
import time
import httpx
from sqlalchemy import select
from database import async_session, PaymentGatewayConfig


async def get_gateway_config():
    async with async_session() as session:
        return await session.scalar(select(PaymentGatewayConfig).limit(1))


def _describe_error(e: Exception, base_url: str) -> str:
    """همیشه یه متن قابل‌فهم برمی‌گردونه، حتی وقتی خودِ exception پیام خالی داره
    (مثلا خطاهای اتصال/DNS که رایجه پیامشون خالی باشه)."""
    detail = str(e).strip()
    if not detail:
        detail = "(پیام خطا خالی بود - احتمالاً مشکل اتصال/DNS/SSL)"
    return f"{type(e).__name__}: {detail} | آدرسی که امتحان شد: {base_url}"


async def create_invoice(amount: int, order_id: str, description: str = "شارژ کیف پول"):
    """
    خروجی: (invoice_dict یا None, متن خطا یا None)
    invoice_dict شامل: uid, payment_url, payable_amount, expires_at
    """
    cfg = await get_gateway_config()
    if not cfg or not cfg.active or not cfg.api_key:
        return None, "درگاه پرداخت تنظیم/فعال نشده"

    url = f"{cfg.base_url.rstrip('/')}/api/v1/invoices"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                headers={"X-API-KEY": cfg.api_key, "Content-Type": "application/json"},
                json={
                    "amount": amount,
                    "fee_mode": cfg.fee_mode or "split",
                    "order_id": order_id,
                    "description": description,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return None, f"پاسخ ناموفق از سرور: {data}"
            return data["data"], None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return None, f"HTTP {e.response.status_code} از {url}: {body or '(بدنه‌ی خالی)'}"
    except Exception as e:
        return None, _describe_error(e, url)


async def get_invoice_status(uid: str):
    """خروجی: (status_dict یا None, متن خطا یا None)"""
    cfg = await get_gateway_config()
    if not cfg or not cfg.api_key:
        return None, "درگاه پرداخت تنظیم نشده"

    url = f"{cfg.base_url.rstrip('/')}/api/v1/invoices/{uid}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"X-API-KEY": cfg.api_key})
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                return None, f"پاسخ ناموفق از سرور: {data}"
            return data["data"], None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return None, f"HTTP {e.response.status_code} از {url}: {body or '(بدنه‌ی خالی)'}"
    except Exception as e:
        return None, _describe_error(e, url)


async def verify_invoice(uid: str):
    """تایید نهایی پرداخت - خروجی: (paid: bool, status_dict یا None, متن خطا یا None)"""
    cfg = await get_gateway_config()
    if not cfg or not cfg.api_key:
        return False, None, "درگاه پرداخت تنظیم نشده"

    url = f"{cfg.base_url.rstrip('/')}/api/v1/invoices/{uid}/verify"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers={"X-API-KEY": cfg.api_key})
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("paid")), data.get("data"), None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return False, None, f"HTTP {e.response.status_code} از {url}: {body or '(بدنه‌ی خالی)'}"
    except Exception as e:
        return False, None, _describe_error(e, url)


def make_order_id(user_id: int) -> str:
    return f"lidso_{user_id}_{time.time_ns()}"
