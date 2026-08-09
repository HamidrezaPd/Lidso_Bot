# راهنمای اجرای دائمی ربات روی سرور Ubuntu با systemd

این فایل رو با نام lidso-bot.service توی مسیر /etc/systemd/system/ کپی کن،
مسیرها و یوزر رو با مقادیر واقعی سرورت جایگزین کن.

---

[Unit]
Description=Lidso VPN Shop Bot
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=lidsobot
WorkingDirectory=/home/lidsobot/Lidso_Bot
ExecStart=/home/lidsobot/Lidso_Bot/venv/bin/python3 main.py
Restart=always
RestartSec=5
StandardOutput=append:/home/lidsobot/Lidso_Bot/bot.log
StandardError=append:/home/lidsobot/Lidso_Bot/bot.log

[Install]
WantedBy=multi-user.target

---

## مراحل نصب روی سرور (Ubuntu 24.04)

1. یه یوزر جدا (نه root) برای اجرای ربات بساز:
   sudo adduser lidsobot

2. پروژه رو توی /home/lidsobot/Lidso_Bot آپلود کن (مثلاً با scp یا git).

3. با همون یوزر وارد شو و venv بساز:
   su - lidsobot
   cd Lidso_Bot
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

4. فایل .env رو بساز (از .env.example کپی کن و پر کن) - PROXY_URL رو خالی بذار
   چون سرور خارج فیلتر نیست، و DATABASE_URL رو به آدرس PostgreSQL ست کن.

5. یه بار دستی تست کن که بالا میاد:
   python3 main.py
   (اگه خطایی نبود، با Ctrl+C ببندش)

6. فایل سرویس بالا رو بساز:
   sudo nano /etc/systemd/system/lidso-bot.service
   (محتوای بالا رو بچسبون و مسیرها رو با مسیر واقعی سرورت match کن)

7. سرویس رو فعال و روشن کن:
   sudo systemctl daemon-reload
   sudo systemctl enable lidso-bot
   sudo systemctl start lidso-bot

8. وضعیت و لاگ رو چک کن:
   sudo systemctl status lidso-bot
   tail -f /home/lidsobot/Lidso_Bot/bot.log

از این به بعد، ربات با هر ریبوت سرور خودش بالا میاد، و اگه به هر دلیلی crash کنه،
systemd خودکار ظرف 5 ثانیه دوباره اجراش می‌کنه.

## دستورات مفید بعدی
- توقف:                sudo systemctl stop lidso-bot
- ری‌استارت (بعد از آپدیت کد):  sudo systemctl restart lidso-bot
- دیدن لاگ زنده:        sudo journalctl -u lidso-bot -f
