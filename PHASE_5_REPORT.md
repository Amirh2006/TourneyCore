# گزارش فاز ۵: ابزارهای خط فرمان مدیر (Admin CLI)

## هدف فاز

ساخت یک رابط خط فرمان کامل برای مدیریت تورنمنت، به‌گونه‌ای که مدیر بتواند بدون دست‌زدن به فایل‌های JSON کل چرخه‌ی مسابقه را کنترل کند:

1. ساخت تورنمنت و باز کردن ثبت‌نام
2. افزودن بازیکن و تولید کد ورود
3. ثبت‌نام بازیکن با کد ورود و شناسه‌ی تلگرام
4. ساخت براکت حذفی دوگانه
5. شروع مسابقه، ثبت برد و ثبت عدم حضور (walkover)
6. مشاهده‌ی وضعیت تورنمنت، بازیکنان و براکت

فایل‌های اصلی:

- `cli/admin.py` — پیاده‌سازی CLI
- `cli/__init__.py` — بسته‌ی خط فرمان
- `tests/test_cli.py` — تست‌های یکپارچه

---

## ۱. اجرا

```powershell
$env:PYTHONPATH='D:\Projects\TourneyCore'
.\venv\Scripts\python.exe -m cli.admin --help
```

مسیر پیش‌فرض داده‌ها `data/` است. برای استفاده از مسیر دیگر:

```powershell
.\venv\Scripts\python.exe -m cli.admin --data-dir C:\tournaments --help
```

---

## ۲. دستورات

| دستور | کار |
|--------|------|
| `init NAME --max-players N --description TEXT` | ساخت تورنمنت و باز کردن ثبت‌نام |
| `add-player SYSTEM_ID NAME` | افزودن بازیکن و چاپ کد ورود |
| `register ENTRY_CODE --telegram-id ID --username NAME` | تبدیل کد ورود به بازیکن ثبت‌نام‌شده |
| `list-systems` | فهرست تورنمنت‌ها |
| `list-players SYSTEM_ID` | فهرست بازیکنان با وضعیت و رکورد برد/باخت |
| `seed SYSTEM_ID` | ساخت براکت حذفی دوگانه |
| `list-matches SYSTEM_ID` | نمایش براکت |
| `show-bracket SYSTEM_ID` | معادل `list-matches` |
| `start MATCH_ID` | شروع یک مسابقه‌ی `READY` |
| `win MATCH_ID PLAYER_ID --score1 A --score2 B [--notify]` | ثبت نتیجه‌ی عادی |
| `walkover MATCH_ID PLAYER_ID [--notify]` | ثبت برد با عدم حضور حریف |

هر دستور در صورت خطا پیام مناسب روی `stderr` چاپ می‌کند و با کد خروج `1` پایان می‌یابد.

---

## ۳. چرخه‌ی کامل یک تورنمنت

```powershell
# ۱. ساخت تورنمنت
python -m cli.admin init "Friday Cup" --max-players 4
# Created tournament sys_... (Friday Cup).

# ۲. افزودن بازیکنان
python -m cli.admin add-player sys_... Alice
# Entry code: V38CQWQW

python -m cli.admin add-player sys_... Bob
# Entry code: FP5K3PTY

# ۳. ثبت‌نام بازیکنان
python -m cli.admin register V38CQWQW --telegram-id 123456789 --username alice
python -m cli.admin register FP5K3PTY --telegram-id 987654321 --username bob

# ۴. ساخت براکت
python -m cli.admin seed sys_...
# Seeded 4 players into 7 matches.

# ۵. مشاهده‌ی براکت
python -m cli.admin list-matches sys_...

# ۶. شروع و ثبت نتیجه
python -m cli.admin start m_...
python -m cli.admin win m_... plr_... --score1 3 --score2 1
```

پس از هر برد:

- نتیجه در `matches.json` ذخیره می‌شود.
- برنده و بازنده به مسابقات بعدی منتقل می‌شوند.
- رکورد برد/باخت بازیکنان به‌روزرسانی می‌شود.
- بازیکن با دو باخت `ELIMINATED` می‌شود.
- با پایان تورنمنت، وضعیت سیستم به `completed` تغییر می‌کند.

---

## ۴. منطق ذخیره‌سازی

CLI مستقیماً به فایل‌ها دست نمی‌زند و از `StorageManager` استفاده می‌کند:

- `create_system` / `create_player` / `redeem_entry_code`
- `save_matches` هنگام ساخت براکت
- `save_match` برای هر مسابقه‌ای که موتور براکت تغییر داده است
- `save_player` برای به‌روزرسانی رکورد بازیکنان

نکته‌ی مهم: توابع `require_match` و `list_matches` هر کدام نمونه‌ی جداگانه‌ای از مدل را برمی‌گردانند. بنابراین نتیجه‌ی موتور براکت باید از نمونه‌ی موجود در `list_matches` خوانده و ذخیره شود؛ در غیر این صورت نسخه‌ی قدیمی روی دیسک نوشته می‌شود. این مورد در تست‌ها پوشش داده شده است.

---

## ۵. تست‌ها

فایل `tests/test_cli.py` شامل ۵ تست یکپارچه است:

- اجرای کامل تورنمنت ۴ نفره و بررسی ذخیره‌شدن صحیح نتیجه‌ها روی دیسک
- اجرای فینال بزرگ با بازی تکراری
- ساخت براکت برای تعداد فرد و بررسی bye
- اجرای کامل تورنمنت ۳ نفره
- رد کردن ثبت نتیجه با بازیکن غیرمجاز

اجرای همه‌ی تست‌ها:

```powershell
$env:PYTHONPATH='D:\Projects\TourneyCore'
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

نتیجه: **۳۹ تست موفق** در کل پروژه (۱۹ براکت + ۵ CLI + ۱۵ ربات).

---

## ۶. اعلان تلگرام

با افزودن `--notify` به `win` یا `walkover`، بازیکنانی که مسابقه‌ی بعدی‌شان تازه `READY` شده است پیام تلگرام دریافت می‌کنند:

```powershell
$env:TOURNEYCORE_BOT_TOKEN='123456:ABC...'
python -m cli.admin win m_... plr_... --notify
```

اگر توکن تنظیم نشده باشد، فقط یک هشدار چاپ می‌شود و ثبت نتیجه انجام می‌شود.

---

## ۷. محدودیت‌ها

- CLI فعلاً فقط برای مدیر است؛ ثبت‌نام واقعی از طریق ربات تلگرام در فاز ۶ اضافه شد.

---

## ۸. وضعیت فازها

| فاز | بخش | وضعیت |
|------|------|-------|
| ۱ | ساختار پروژه | ✅ |
| ۲ | مدل‌های داده Pydantic | ✅ |
| ۳ | ذخیره‌سازی JSON غیرهمزمان | ✅ |
| ۴ | موتور براکت حذفی دوگانه | ✅ |
| ۵ | ابزارهای خط فرمان مدیر | ✅ |
| ۶ | هندلرهای ربات تلگرام | ✅ |
| ۷ | فرانت‌اند Mini-App | ⏳ بعدی |
