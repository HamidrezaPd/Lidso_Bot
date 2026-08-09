"""
تولید عکس QR کد برای لینک اشتراک - یه جای مشترک برای همه‌ی جاهایی که لینک سرویس تحویل داده میشه
(خرید عادی، تست رایگان، مشاهده‌ی مجدد از «سرویس‌های من»).
"""
import io

import qrcode
from aiogram.types import BufferedInputFile


def generate_qr_photo(link: str, filename: str = "qrcode.png") -> BufferedInputFile:
    """از یه لینک، عکس QR کد (PNG) می‌سازه و آماده‌ی ارسال با send_photo برمی‌گردونه."""
    qr = qrcode.QRCode(
        version=None,  # خودکار بر اساس طول لینک تعیین میشه
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return BufferedInputFile(buf.read(), filename=filename)
