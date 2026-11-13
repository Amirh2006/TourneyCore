# درخواست許可 فاز ۵: ابزارهای خط فرمان Admin (CLI)

## وضعیت جاری

تمام فازهای ۱ تا ۴ کامل شده‌اند:

| فاز | اسم | نتیجه |
|----|-----|-------|
| ۱ | scaffold پروژه | ساختار پوشه‌ها، virtual environment، requirements.txt، Git روی `develop` و `main` |
| ۲ | Core Data Models | `core/models.py` با Pydantic: `GamingSystem`, `Player`, `Match` + Enumها، اعتبارسنجی، round-trip JSON |
| ۳ | Async Storage Manager | `core/storage.py` با `aiofiles`: `JsonCollection`, `StorageManager`, ذخیره اتمیک، validation، recovery از خراب بودن فایل |
| ۴ | Double Elimination Logic | `core/bracket.py`: `advance_match`, `is_tournament_over`, `find_next_match`, `make_grand_final` |

## فاز ۵: Admin CLI Tools

هدف این Faze، ابزارهای خط فرمان admin برای کنترل حالت کامل turne‌رمنت از رоуتم است. عملیات‌های مورد نیاز:

### ۱. `init system_name max_players`
- ساخت یک GamingSystem جدید با `status=registration`
- افزودن آن به `systems.json` Storage
- بازیکنان را pudding/ready 상태로 نگه داشت

### ۲. `generate-code system_name max_players`
- تولید یک کد ورود یکتا (Scenario A) توسط StorageManager
- ثبت codes Database
- نمایش کد روی Konsol

### ۳. `register player_name system_id code telegram_id`
- ریدم کردن کد ورود توسط بازیکن در ربات تلگرام
- زوج کردن telegram_id با رکورد Player
- تغییر وضعیت به `registered` و `active`

### ۴. `start match_id`
- تعیین لیگ‌های relevant ( locating the match via ID)
- اگر match `PENDING` است، set `status=READY` و `started_at=now`
- اگر بازیکن‌های لازم را ندارید، خطا بدهید

### ۵. `win match_id player_id`
- ثبت پیروزی بازیکن در آخرین مسابقه فعال
- chamge `losses` / `wins` بازیکن
- lógica elimination: اگر بازیکن ۲ باخت کسب کرد، `status=eliminated` و `eliminated_at=now`
- پیشروی برنده در براکت (با `advance_match` از فاز ۴)
- ذخیره автоматически大会matches

### ۶. `walkover match_id player_id`
- 同上 به `win`، اما با `WinMethod.WALKOVER`
- مناسب برای عدم حضور بازیکن یا Walkover اداری

### ۷. `list-players system_id`
- لیست تمام بازیکنان یک سیستم نمایش دادن

### ۸. `list-matches system_id`
- لیستMatches نمایش دادن (شامل stade‌های PENDING/READY/ACTIVE/COMPLETED)

## ساختار اعمال

1. **زیرشاخه**: `git checkout -b feature/phase-5 develop`
2. **ایجاد**: `cli/admin.py` (یا مستقیم در `main.py`) با توابع CLI بالا
3. **Commitهای اتمیک**: هر گزاره functionality خود را commit می‌کند
4. **Merge**: 
   - زیرشاخه را merge به `develop`
   - سپس `develop` را merge به `main`
   - push به ریموت
5. **گزارش فارسی**: `PHASE_5_REPORT.md`

## خروجی انتظار

پس از اتمام فاز ۵،architecture TourneyCore کامل خواهد بود:
- ✅ مدل داده Pydantic (فاز ۲)
- ✅ ذخیره‌سازی JSON_async (فاز ۳)
- ✅ موتور براکتDouble Elimination (فاز ۴)
- ✅ ابزارAdmin CLI برای مدیریت turne‌ment (فاز ۵)
- ✅ در فاز بعدی (فاز ۶): Bot handlers + code check-in
- ✅ در فاز بعدی (فاز ۷): Frontend Mini-App دو چهره

---

**لطفاً دستور خود را بدهید:**

- ✅ **Proceed to Phase 5**: من آماده‌ام تا `feature/phase-5` بسازم و `cli/admin.py` را پیاده‌سازی کنم.
- ❌ **Wait / Feedback**: دیدگاه‌ها یا تغییرات درwajگ دارید.

**Commit message convention**: `feat: add CLI admin tools for tournament management` (یا مشابه conventional commits).

---

Today: Fri Nov 13 2026 | Phase 4 completed ✅ | Ready for Phase 5 input