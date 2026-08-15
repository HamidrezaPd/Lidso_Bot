"""
لایه‌ی اتصال به پنل‌های مختلف وی‌پی‌ان.

هر ServicePlan یک panel_id داره که به جدول Panel وصله. توی جدول Panel مشخص میکنی
panel_type چیه (marzban / pasarguard / youpanel) و url/username/password.
این فایل بر اساس panel_type، پیاده‌سازی درست رو صدا می‌زنه.

⚠️ توجه مهم:
- Marzban و PasarGuard الان کاملاً از هم جدا پیاده‌سازی شدن (هرکدوم توابع مستقل خودشون رو دارن)،
  با اینکه نقطه‌ی شروعشون یکی بوده (چون API شبیه‌همه). این یعنی از این به بعد اگه یه تغییر یا
  فیکس فقط برای یکی از این دو پنل لازم باشه، بدون تاثیر روی اون یکی قابل انجامه.
- YouPanel یه پنل کمتر شناخته‌شده‌ست و مستندات API عمومی و قابل اعتمادی ازش پیدا نکردم،
  پس فعلا به صورت STUB (شبیه‌سازی) گذاشتمش. وقتی مستندات API پنلت رو داشتی (یا لینکش رو
  بدی) دقیق پیاده‌ش می‌کنم - تابع youpanel_create/renew/delete رو فقط باید عوض کنیم.
"""
import time
import logging
from datetime import datetime, timedelta, timezone
import httpx
from urllib.parse import urlparse
from sqlalchemy import select
from database import async_session, ServiceOrder, StockConfig, Panel

# کش توکن پنل توی حافظه - برای سرعت بیشتر (دیگه هر عملیات نیازی به لاگین دوباره نداره)
_token_cache: dict[int, tuple[str, float]] = {}  # panel.id -> (token, expires_at_unix)
TOKEN_TTL_SECONDS = 20 * 60


def _fix_subscription_link(panel: Panel, raw_link: str) -> str:
    """
    لینک اشتراک پنل را به URL قابل استفاده تبدیل می‌کند.

    Marzban ممکن است subscription_url را به صورت relative مثل:
        /sub/xxxxx
    برگرداند. در این حالت باید دامنه خود پنل به آن اضافه شود.

    PasarGuard ممکن است URL کامل با دامنه‌ای متفاوت از دامنه پنل برگرداند
    (مثلاً api.rain.fail). در این حالت URL را بدون تغییر نگه می‌داریم.
    """
    if not raw_link:
        return raw_link

    # لینک‌های مستقیم کانفیگ را دست‌نخورده نگه دار
    if raw_link.startswith(("vless://", "vmess://", "trojan://", "ss://")):
        return raw_link

    # Relative subscription URL مثل /sub/xxxxx
    if raw_link.startswith("/"):
        return f"{panel.url.rstrip('/')}{raw_link}"

    # URL کامل را بدون تغییر نگه دار
    return raw_link


async def _marzban_list_usernames(panel: Panel, prefix: str) -> list[str]:
    """
    همه‌ی یوزرنیم‌های پنل که با این پیشوند شروع میشن رو برمی‌گردونه (برای پیدا کردن
    بزرگترین عدد سریالی، حتی اگه دستی توی خودِ پنل ساخته شده باشن).

    ⚠️ نکته‌ی مهم Marzban: اگه حساب متصل‌شده (username/password تنظیمات پنل) یه ادمین sudo
    نباشه، این endpoint فقط کاربرهای متعلق به همون ادمین رو برمی‌گردونه (یا اصلاً 403 میده) -
    نه همه‌ی کاربرهای پنل. این باعث میشه شماره‌گذاری خودکار کانفیگ‌ها با کاربرهایی که ادمین‌های
    دیگه (یا داشبورد وب) ساختن تصادم کنه. اگه این مشکل رو داری، حساب پنل رو به sudo/admin کامل تغییر بده.
    """
    token = await _marzban_get_token(panel)
    usernames = []
    offset = 0
    limit = 200
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            resp = await client.get(
                f"{panel.url.rstrip('/')}/api/users",
                headers={"Authorization": f"Bearer {token}"},
                params={"offset": offset, "limit": limit},
            )
            if resp.status_code == 403:
                raise PermissionError(
                    "403 Forbidden روی GET /api/users - حساب پنل به‌احتمال زیاد ادمین sudo نیست "
                    "و فقط کاربرهای خودش رو می‌بینه، نه همه‌ی پنل رو."
                )
            resp.raise_for_status()
            data = resp.json()
            users = data.get("users", [])
            usernames.extend(u.get("username", "") for u in users if u.get("username", "").startswith(prefix))
            if len(users) < limit:
                break
            offset += limit
    return usernames


_PANEL_LIST_MAP = {}  # پر میشه پایین فایل بعد از تعریف _youpanel_list_usernames


async def get_next_config_name(category_prefix: str, volume_tag: str, panel: Panel = None) -> str:
    """
    محاسبه‌ی نام داینامیک بعدی، مثلا LidsoPrime_10gb_101

    منبع اصلی همیشه خودِ پنل واقعیه (اگه در دسترس باشه) - یعنی اگه یه کانفیگ رو دستی از
    توی پنل حذف کرده باشی، دیگه جزو شماره‌های "استفاده‌شده" حساب نمیشه و اون عدد آزاد میشه.
    فقط وقتی پنل در دسترس نبود (یا اصلاً پلن به پنلی وصل نبود، مثلا تحویل دستی)، از
    تاریخچه‌ی دیتابیس خودمون به‌عنوان جایگزین استفاده می‌کنیم.
    """
    prefix = f"{category_prefix}_{volume_tag}_"
    max_num = 100

    panel_names = None
    if panel:
        try:
            fn = _PANEL_LIST_MAP.get(panel.panel_type)
            if fn:
                panel_names = await fn(panel, prefix)
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"⚠️ نتونستم لیست کاربرهای پنل «{panel.name}» رو بگیرم (fallback به تاریخچه‌ی دیتابیس محلی): {e}\n"
                f"اگه این خطا 403 Forbidden بود، احتمالاً حساب متصل‌شده به این پنل سطح دسترسی sudo/admin "
                f"کامل نداره - در Marzban، ادمین‌های غیر-sudo فقط کاربرهای خودشون رو می‌بینن که باعث میشه "
                f"شماره‌گذاری خودکار کانفیگ‌ها با کاربرهای واقعی پنل تصادم کنه (409 موقع ساخت)."
            )
            panel_names = None  # اگه گرفتن لیست fail شد، میریم سراغ fallback دیتابیس

    if panel_names is not None:
        source_names = panel_names
    else:
        async with async_session() as session:
            order_names = (await session.execute(
                select(ServiceOrder.config_name).where(ServiceOrder.config_name.like(f"{prefix}%"))
            )).scalars().all()
            stock_names = (await session.execute(
                select(StockConfig.config_name).where(StockConfig.config_name.like(f"{prefix}%"))
            )).scalars().all()
        source_names = list(order_names) + list(stock_names)

    for name in source_names:
        try:
            num = int(name.split("_")[-1])
            if num > max_num:
                max_num = num
        except (ValueError, AttributeError):
            continue

    return f"{prefix}{max_num + 1}"


def volume_tag_from_plan(plan) -> str:
    """LidsoPrime_10gb_101 یا LidsoUnlimited_2user_101

    برای Unlimited، HWID تعداد واقعی کاربران مجاز را مشخص می‌کند.
    HWID را در اولویت می‌گذاریم تا حتی پلن‌های قدیمی که max_users آن‌ها
    به اشتباه 1 مانده، دیگر با نام 1user ساخته نشوند.
    """
    if plan.category == "LidsoUnlimited" or plan.volume_gb == 0:
        user_count = getattr(plan, "hwid_limit", 0) or getattr(plan, "max_users", 1) or 1
        return f"{user_count}user"
    return f"{plan.volume_gb}gb"


# ==================== Marzban / PasarGuard (سازگار) ====================

async def _marzban_get_token(panel: Panel, force_refresh: bool = False) -> str:
    cached = _token_cache.get(panel.id)
    if cached and not force_refresh and cached[1] > time.time():
        return cached[0]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{panel.url.rstrip('/')}/api/admin/token",
            data={"username": panel.username, "password": panel.password},
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]

    _token_cache[panel.id] = (token, time.time() + TOKEN_TTL_SECONDS)
    return token


async def _marzban_get_user(panel: Panel, config_name: str) -> dict:
    token = await _marzban_get_token(panel)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


def _get_panel_group_ids(panel: Panel) -> list:
    """
    ✅ طبق مستندات واقعی: group_ids یه لیست ثابت شماره‌ست (مثلا [1]) که موقع ساخت کاربر
    فرستاده میشه - نیازی به گرفتن لیست زنده از پنل نیست. از /admin قابل تغییره.
    """
    raw = (panel.group_ids or "1").strip()
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids or [1]


async def _marzban_get_inbound_tags(panel: Panel, protocol: str) -> list[str]:
    """
    اگه ادمین دستی تگ اینباند رو توی تنظیمات پنل مشخص نکرده باشه، خودمون از
    GET /api/inbounds می‌گیریم و اولین تگِ همون پروتکل رو استفاده می‌کنیم.
    """
    if panel.inbound_tags and panel.inbound_tags.strip():
        return [t.strip() for t in panel.inbound_tags.split(",") if t.strip()]

    token = await _marzban_get_token(panel)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{panel.url.rstrip('/')}/api/inbounds",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    protocol_inbounds = data.get(protocol) or []
    tags = [ib.get("tag") for ib in protocol_inbounds if ib.get("tag")]
    if not tags:
        raise ValueError(
            f"هیچ اینباندی برای پروتکل «{protocol}» توی پنل «{panel.name}» پیدا نشد. "
            f"از تنظیمات پنل توی بات ادمین، تگ اینباند رو دستی وارد کن."
        )
    return tags


async def _marzban_create(panel: Panel, config_name: str, plan) -> str:
    """
    ✅ طبق مستندات رسمی API که برای این پنل (Marzban استاندارد/MMD) داده شده:
    - حجم بر حسب بایت (GB * 1024^3)، expire بر حسب Unix timestamp UTC (0 = نامحدود)
    - بدنه از proxies/inbounds استفاده می‌کنه، نه group_ids/proxy_settings (که مخصوص PasarGuard بود)
    - هیچ فیلد استانداردی برای HWID توی این نسخه از API وجود نداره؛ اگه پنلت پشتیبانی می‌کنه
      باید از /docs همون پنل چک بشه - فعلاً نادیده گرفته میشه (توی PasarGuard که hwid_limit داره فرق داره)
    """
    token = await _marzban_get_token(panel)
    data_limit = int(plan.volume_gb * (1024 ** 3)) if plan.volume_gb else 0
    # expire اینجا Unix timestamp هست (نه ISO مثل PasarGuard) - طبق مستندات رسمی این پنل
    if plan.duration_days == 0:
        expire_value = 0
    else:
        expire_value = int((datetime.now(timezone.utc) + timedelta(days=plan.duration_days)).timestamp())

    protocol = (panel.protocol or "vless").strip().lower()
    tags = await _marzban_get_inbound_tags(panel, protocol)

    payload = {
        "username": config_name,
        "status": "active",
        "data_limit": data_limit,
        "data_limit_reset_strategy": "no_reset",
        "expire": expire_value,
        "on_hold_expire_duration": None,
        "proxies": {protocol: {}},
        "inbounds": {protocol: tags},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{panel.url.rstrip('/')}/api/user",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )

        if resp.status_code == 409:
            # 409 یعنی این username از قبل توی پنل وجود داره (مثلا یه بار قبلاً با موفقیت ساخته
            # شده ولی فقط گرفتن جواب/لینکش fail شده بود). به‌جای خطا دادن، مستقیم اطلاعاتشو می‌گیریم.
            try:
                result = await _marzban_get_user(panel, config_name)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    # این یعنی username از قبل روی پنل وجود داره ولی حساب متصل‌شده (username/password
                    # که توی تنظیمات پنل دادی) اجازه‌ی دیدن این کاربر رو نداره - مثلاً چون یه ادمین
                    # دیگه (نه ادمین اصلی/sudo) ساختتش، یا این username قبلاً دستی روی پنل ساخته شده.
                    raise ValueError(
                        f"یوزرنیم «{config_name}» از قبل روی پنل Marzban وجود داره، ولی حساب متصل‌شده "
                        f"به این پنل توی بات (username/password تنظیمات پنل) اجازه‌ی مشاهده‌ش رو نداره "
                        f"(403 Forbidden). این معمولاً یعنی حساب پنل باید سطح دسترسی sudo/admin کامل داشته "
                        f"باشه، یا این یوزرنیم قبلاً با یه حساب ادمین دیگه ساخته شده. لطفاً حساب متصل‌شده "
                        f"به این پنل رو با یه حساب ادمین sudo (نه ادمین محدود) عوض کن."
                    )
                raise
            raw_link = result.get("subscription_url") or (result.get("links") or [""])[0]
            if not raw_link:
                raise ValueError(f"پنل Marzban لینک اشتراکی برای کاربر {config_name} برنگردوند.")
            return _fix_subscription_link(panel, raw_link)

        if resp.status_code == 422:
            raise ValueError(f"داده‌های ارسالی به پنل نامعتبره (422). پاسخ پنل: {resp.text[:300]}")

        resp.raise_for_status()
        result = resp.json()
        raw_link = result.get("subscription_url") or (result.get("links") or [""])[0]
        if not raw_link:
            raise ValueError(f"پنل Marzban بعد از ساخت کاربر لینک اشتراکی برنگردوند. پاسخ خام: {result}")
        return _fix_subscription_link(panel, raw_link)


async def _marzban_renew(panel: Panel, config_name: str, plan) -> None:
    token = await _marzban_get_token(panel)
    data_limit = int(plan.volume_gb * (1024 ** 3)) if plan.volume_gb else 0
    # duration_days=0 یعنی نامحدود (بدون تاریخ انقضا) - expire اینجا Unix timestamp UTC هست
    # (طبق مستندات رسمی این پنل) نه ISO format که PasarGuard استفاده می‌کنه
    if plan.duration_days == 0:
        expire_value = 0
    else:
        expire_value = int((datetime.now(timezone.utc) + timedelta(days=plan.duration_days)).timestamp())

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "data_limit": data_limit,
                "data_limit_reset_strategy": "no_reset",
                "expire": expire_value,
                "status": "active",
                "on_hold_expire_duration": None,
            },
        )
        resp.raise_for_status()

        # ⚠️ نکته‌ی مهم: توی Marzban واقعی، فیلد used_traffic توی همین PUT عملاً نادیده گرفته
        # میشه و ریست نمیشه. ریست واقعی حجم مصرفی از طریق یک endpoint جداگانه انجام میشه:
        try:
            reset_resp = await client.post(
                f"{panel.url.rstrip('/')}/api/user/{config_name}/reset",
                headers={"Authorization": f"Bearer {token}"},
            )
            # بعضی پنل‌ها اگه از قبل صفر بوده ممکنه استاتوس متفاوتی بدن؛ کل عملیات رو fail نکن
        except Exception:
            pass


async def _marzban_delete(panel: Panel, config_name: str) -> None:
    token = await _marzban_get_token(panel)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        # اگه از قبل حذف شده بود (404) مشکلی نیست
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()


# ==================== PasarGuard (جدا از Marzban) ====================
# ⚠️ همون منطق Marzban به‌عنوان نقطه‌ی شروع کپی شده (چون فورک سازگاریه) ولی الان کاملاً
# مستقل از توابع Marzban هست. اگه بعداً یه اندپوینت یا فیلد PasarGuard فرق کرد، فقط همین
# بخش رو عوض کن - به Marzban دست نمی‌خوره و برعکس.

async def _pasarguard_get_token(panel: Panel, force_refresh: bool = False) -> str:
    cached = _token_cache.get(panel.id)
    if cached and not force_refresh and cached[1] > time.time():
        return cached[0]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{panel.url.rstrip('/')}/api/admin/token",
            data={"username": panel.username, "password": panel.password},
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]

    _token_cache[panel.id] = (token, time.time() + TOKEN_TTL_SECONDS)
    return token


async def _pasarguard_get_user(panel: Panel, config_name: str) -> dict:
    token = await _pasarguard_get_token(panel)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _pasarguard_create(panel: Panel, config_name: str, plan) -> str:
    token = await _pasarguard_get_token(panel)
    data_limit = int(plan.volume_gb * (1024 ** 3)) if plan.volume_gb else 0
    if plan.duration_days == 0:
        expire_value = 0
    else:
        expire_value = (datetime.now(timezone.utc) + timedelta(days=plan.duration_days)).isoformat(timespec="seconds")

    payload = {
        "username": config_name,
        "status": "active",
        "data_limit": data_limit,
        "data_limit_reset_strategy": "no_reset",
        "expire": expire_value,
        "on_hold_expire_duration": None,
        "note": "",
        "group_ids": _get_panel_group_ids(panel),
        "proxy_settings": {"shadowsocks": {"method": "chacha20-ietf-poly1305"}},
    }

    if getattr(plan, "hwid_limit", 0):
        payload["hwid_limit"] = plan.hwid_limit

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{panel.url.rstrip('/')}/api/user",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )

        if resp.status_code == 409:
            result = await _pasarguard_get_user(panel, config_name)
            raw_link = result.get("subscription_url") or (result.get("links") or [""])[0]
            if not raw_link:
                raise ValueError(f"پنل PasarGuard لینک اشتراکی برای کاربر {config_name} برنگردوند.")
            return _fix_subscription_link(panel, raw_link)

        resp.raise_for_status()
        result = resp.json()
        raw_link = result.get("subscription_url") or (result.get("links") or [""])[0]
        if not raw_link:
            raise ValueError(f"پنل PasarGuard بعد از ساخت کاربر لینک اشتراکی برنگردوند. پاسخ خام: {result}")
        return _fix_subscription_link(panel, raw_link)


async def _pasarguard_renew(panel: Panel, config_name: str, plan) -> None:
    token = await _pasarguard_get_token(panel)
    data_limit = int(plan.volume_gb * (1024 ** 3)) if plan.volume_gb else 0
    if plan.duration_days == 0:
        expire_value = 0
    else:
        expire_value = (datetime.now(timezone.utc) + timedelta(days=plan.duration_days)).isoformat(timespec="seconds")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "data_limit": data_limit,
                "data_limit_reset_strategy": "no_reset",
                "expire": expire_value,
                "status": "active",
                "on_hold_expire_duration": None,
            },
        )
        resp.raise_for_status()

        try:
            await client.post(
                f"{panel.url.rstrip('/')}/api/user/{config_name}/reset",
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception:
            pass


async def _pasarguard_delete(panel: Panel, config_name: str) -> None:
    token = await _pasarguard_get_token(panel)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"{panel.url.rstrip('/')}/api/user/{config_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()


async def _pasarguard_list_usernames(panel: Panel, prefix: str) -> list[str]:
    token = await _pasarguard_get_token(panel)
    usernames = []
    offset = 0
    limit = 200
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            resp = await client.get(
                f"{panel.url.rstrip('/')}/api/users",
                headers={"Authorization": f"Bearer {token}"},
                params={"offset": offset, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            users = data.get("users", [])
            usernames.extend(u.get("username", "") for u in users if u.get("username", "").startswith(prefix))
            if len(users) < limit:
                break
            offset += limit
    return usernames


# ==================== YouPanel (STUB - نیاز به مستندات واقعی) ====================

async def _youpanel_create(panel: Panel, config_name: str, plan) -> str:
    # TODO: جایگزین کن با API واقعی YouPanel وقتی مستنداتش رو داشتی
    return f"{panel.url.rstrip('/')}/sub/{config_name}"


async def _youpanel_renew(panel: Panel, config_name: str, plan) -> None:
    # TODO
    return None


async def _youpanel_delete(panel: Panel, config_name: str) -> None:
    # TODO
    return None


async def _youpanel_list_usernames(panel: Panel, prefix: str) -> list:
    # TODO: وقتی API واقعی YouPanel رو داشتیم اینجا پیاده می‌کنیم
    return []


_PANEL_LIST_MAP.update({
    "marzban": _marzban_list_usernames,
    "pasarguard": _pasarguard_list_usernames,
    "youpanel": _youpanel_list_usernames,
})


# ==================== Dispatcher عمومی ====================

_CREATE_MAP = {
    "marzban": _marzban_create,
    "pasarguard": _pasarguard_create,
    "youpanel": _youpanel_create,
}
_RENEW_MAP = {
    "marzban": _marzban_renew,
    "pasarguard": _pasarguard_renew,
    "youpanel": _youpanel_renew,
}
_DELETE_MAP = {
    "marzban": _marzban_delete,
    "pasarguard": _pasarguard_delete,
    "youpanel": _youpanel_delete,
}


async def create_panel_account(panel: Panel, config_name: str, plan) -> str:
    """یک کانفیگ جدید توی پنل مشخص‌شده می‌سازه و لینک ساب رو برمی‌گردونه."""
    fn = _CREATE_MAP.get(panel.panel_type)
    if not fn:
        raise ValueError(f"نوع پنل ناشناخته: {panel.panel_type}")
    return await fn(panel, config_name, plan)


async def renew_panel_account(panel: Panel, config_name: str, plan) -> None:
    """مدت و حجم یک کانفیگ موجود رو ریست می‌کنه (تمدید)."""
    fn = _RENEW_MAP.get(panel.panel_type)
    if not fn:
        raise ValueError(f"نوع پنل ناشناخته: {panel.panel_type}")
    await fn(panel, config_name, plan)


async def delete_panel_account(panel: Panel, config_name: str) -> None:
    """حذف کانفیگ از پنل (برای حذف خودکار بعد از اتمام مهلت)."""
    fn = _DELETE_MAP.get(panel.panel_type)
    if not fn:
        raise ValueError(f"نوع پنل ناشناخته: {panel.panel_type}")
    await fn(panel, config_name)


_GET_USER_MAP = {
    "marzban": _marzban_get_user,
    "pasarguard": _pasarguard_get_user,
}


async def is_panel_account_exhausted(panel: Panel, config_name: str) -> bool:
    """
    برمی‌گردونه True اگه این کاربر توی پنل به سقف حجمش رسیده باشه (مصرف کامل).

    دو روش با هم چک میشه (هرکدوم که True بده کافیه):
    1) فیلد status پنل - Marzban/PasarGuard وقتی کاربر به data_limit برسه، خودشون status رو
       به "limited" تغییر میدن.
    2) مقایسه‌ی عددی used_traffic/data_limit - چون بعضی نسخه‌های پنل ممکنه status رو دیر
       آپدیت کنن یا اسم فیلدش کمی فرق کنه، این یه لایه‌ی دومِ اطمینانه.

    اگه کاربر روی پنل پیدا نشد (قبلاً حذف شده)، True برمی‌گردونه (یعنی از دید ما دیگه
    چیزی برای نگه‌داشتن نیست).
    """
    fn = _GET_USER_MAP.get(panel.panel_type)
    if not fn:
        return False  # پنل‌هایی که get_user ندارن (مثلا youpanel هنوز) - فرض می‌کنیم تمام‌نشده
    try:
        result = await fn(panel, config_name)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return True  # کاربر روی پنل پیدا نشد - از قبل حذف/پاک شده
        raise

    result = result or {}
    status = str(result.get("status", "")).lower()

    # لایه‌ی دوم: مقایسه‌ی عددی مصرف با سقف (اسم فیلد بین نسخه‌های مختلف پنل فرق می‌کنه)
    used = result.get("used_traffic")
    if used is None:
        used = result.get("used")
    data_limit = result.get("data_limit")

    used_mb = round(used / (1024 ** 2), 1) if isinstance(used, (int, float)) else used
    limit_mb = round(data_limit / (1024 ** 2), 1) if isinstance(data_limit, (int, float)) and data_limit else data_limit
    logging.getLogger(__name__).info(
        f"🔎 چک مصرف {config_name}: status={status!r}, مصرف={used_mb}MB, سقف={limit_mb}MB"
    )

    if status in ("limited", "expired", "disabled"):
        return True

    if used is not None and data_limit:  # data_limit=0 یعنی نامحدود، پس نباید چک بشه
        try:
            if float(used) >= float(data_limit):
                return True
        except (TypeError, ValueError):
            pass

    return False
