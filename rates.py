"""
گرفتن نرخ لحظه‌ای تتر (USDT) به تومان، از API عمومی نوبیتکس (بدون نیاز به کلید/توکن).
"""
import httpx


async def get_usdt_toman_rate() -> float | None:
    """قیمت لحظه‌ای هر ۱ تتر به تومان. اگه به هر دلیلی نشد، None برمی‌گردونه."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.nobitex.ir/market/stats",
                json={"srcCurrency": "usdt", "dstCurrency": "rls"},
            )
            resp.raise_for_status()
            data = resp.json()
            rial_price = float(data["stats"]["usdt-rls"]["bestSell"])
            return rial_price / 10  # ریال -> تومان
    except Exception:
        return None
