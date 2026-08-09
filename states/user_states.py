# در حال حاضر state های خرید و کیف پول داخل خود handlers/shop.py و handlers/wallet.py
# تعریف شدن (ساده‌تر برای نگهداری). این فایل برای گسترش‌های بعدی نگه داشته شده.
from aiogram.fsm.state import State, StatesGroup


class ShopStates(StatesGroup):
    choosing_category = State()
    choosing_duration = State()
    choosing_plan = State()
