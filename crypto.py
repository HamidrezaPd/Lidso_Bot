"""
پرداخت ارزی: گرام (روی شبکه TON) و USDC (روی شبکه BEP20/BSC)

نرخ لحظه‌ای: از API عمومی نوبیتکس (apiv2.nobitex.ir)
تایید تراکنش گرام: از TonCenter API v3 (نیاز به کلید رایگان از ربات @tonapibot)
تایید تراکنش USDC: از BscScan API (نیاز به کلید رایگان از bscscan.com)

⚠️ نکته‌ی مهم فنی: شبکه‌ی TON از «کامنت» روی تراکنش پشتیبانی می‌کنه، ولی شبکه‌های
EVM (مثل BSC/BEP20) اصلاً همچین قابلیتی ندارن. برای همین قابلیت کامنت فقط برای
پرداخت گرام/تون معناداره؛ برای USDC تشخیص فقط از روی مبلغ دقیق انجام میشه.
"""
import random
import string
import httpx
from sqlalchemy import select
from database import async_session, CryptoConfig

USDC_BEP20_CONTRACT = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580D"


async def get_crypto_config():
    async with async_session() as session:
        cfg = await session.scalar(select(CryptoConfig).limit(1))
        if not cfg:
            cfg = CryptoConfig()
            session.add(cfg)
            await session.commit()
            await session.refresh(cfg)
        return cfg


def generate_comment() -> str:
    return "LID" + "".join(random.choices(string.digits, k=6))


# ==================== نرخ لحظه‌ای (نوبیتکس) ====================

async def _nobitex_rate(src: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://apiv2.nobitex.ir/market/stats",
                json={"srcCurrency": src, "dstCurrency": "rls"},
            )
            resp.raise_for_status()
            data = resp.json()
            rial_price = float(data["stats"][f"{src}-rls"]["bestSell"])
            return rial_price / 10  # ریال -> تومان
    except Exception:
        return None


async def get_gram_toman_rate():
    return await _nobitex_rate("gram")


async def get_usdc_toman_rate():
    return await _nobitex_rate("usdc")


# ==================== TonCenter (گرام/تون) ====================

def _find_comment_recursive(obj):
    """چون مطمئن نیستیم مسیر دقیق فیلد کامنت توی پاسخ TonCenter کجاست، یه جستجوی
    منعطف انجام می‌دیم تا مقاوم‌تر باشه."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("comment", "text") and isinstance(v, str) and v:
                return v
            found = _find_comment_recursive(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_comment_recursive(item)
            if found:
                return found
    return ""


async def check_ton_payment(address, expected_amount_ton, comment, api_key=None):
    """چک می‌کنه آیا تراکنشی با این کامنت و مبلغ (با ۱٪ تحمل خطا) به این آدرس واریز شده یا نه"""
    if not address:
        return False
    try:
        headers = {"X-Api-Key": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://toncenter.com/api/v3/transactions",
                params={"account": address, "limit": 30},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            for tx in data.get("transactions", []):
                in_msg = tx.get("in_msg", {}) or {}
                try:
                    value_ton = int(in_msg.get("value", 0)) / 1e9
                except (TypeError, ValueError):
                    continue
                tx_comment = _find_comment_recursive(in_msg)
                amount_ok = abs(value_ton - expected_amount_ton) <= max(expected_amount_ton * 0.01, 0.005)
                comment_ok = (not comment) or (comment in tx_comment)
                if amount_ok and comment_ok:
                    return True
            return False
    except Exception:
        return False


# ==================== BscScan (USDC روی BEP20) ====================

async def check_bsc_usdc_payment(address, expected_amount_usdc, api_key=None):
    """چک می‌کنه آیا تراکنش USDC (BEP20) با این مبلغ (با ۱٪ تحمل خطا) به این آدرس واریز شده یا نه.
    ⚠️ BEP20 از کامنت پشتیبانی نمی‌کنه، پس فقط از روی مبلغ دقیق تشخیص میده."""
    if not address:
        return False
    try:
        params = {
            "module": "account", "action": "tokentx",
            "contractaddress": USDC_BEP20_CONTRACT, "address": address,
            "sort": "desc", "page": 1, "offset": 30,
        }
        if api_key:
            params["apikey"] = api_key
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.bscscan.com/api", params=params)
            resp.raise_for_status()
            data = resp.json()
            for tx in data.get("result", []) or []:
                if str(tx.get("to", "")).lower() != address.lower():
                    continue
                decimals = int(tx.get("tokenDecimal", 18))
                value = int(tx.get("value", 0)) / (10 ** decimals)
                if abs(value - expected_amount_usdc) <= max(expected_amount_usdc * 0.01, 0.01):
                    return True
            return False
    except Exception:
        return False
