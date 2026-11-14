# گزارش فاز ۳: مدیر ذخیره‌سازی غیرهمزمان (Async Storage Manager)

## هدف فاز
پیاده‌سازی لایه‌ی ذخیره‌سازی روی فایل‌های JSON محلی، به‌گونه‌ای که:

- تمام I/O غیرمسدودکننده (non-blocking) باشد،
- نوشتن‌ها **اتمیک** باشند و فایل هرگز نیمه‌نوشته روی دیسک نماند،
- دسترسی همزمان ربات تلگرام و CLI باعث از دست رفتن داده نشود،
- داده‌های خوانده‌شده با مدل‌های Pydantic فاز ۲ اعتبارسنجی شوند،
- فایل خراب به‌صورت صریح گزارش شود و هرگز بی‌صدا پاک نشود.

فایل اصلی: `core/storage.py`

---

## ۱. ساختار فایل‌ها

| فایل | محتوا | مدل |
|------|-------|-----|
| `data/systems.json` | تورنمنت‌ها / سیستم‌های بازی | `GamingSystem` |
| `data/users.json` | بازیکنان | `Player` |
| `data/matches.json` | مسابقات | `Match` |

- مسیر پیش‌فرض: `TourneyCore/data/` (`DEFAULT_DATA_DIR`)
- مسیر قابل‌تغییر از طریق آرگومان `data_dir` در `StorageManager`
- دایرکتوری `data/` به `.gitignore` اضافه شد (داده‌ی زمان اجرا نباید کامیت شود)

فرمت هر فایل: یک **آرایه JSON** از اشیای مدل (خوانا، با indentation دو فاصله و خط جدید پایانی) تا مستقیماً قابل مصرف توسط فرانت‌اند باشد.

---

## ۲. نوشتن اتمیک (`atomic_write_text`)

```
[محتوا] → فایل موقت یکتا (.systems.json.<uuid>.tmp)
        → write + flush
        → os.fsync (در thread جدا)
        → os.replace(temp, target)   ← جابه‌جایی اتمیک
```

- `os.replace` روی ویندوز و POSIX جایگزینی اتمیک انجام می‌دهد.
- در صورت خطا، فایل موقت پاک می‌شود (`try/except BaseException`).
- تمام توابع مسدودکننده (`mkdir`, `fsync`, `os.replace`, `unlink`) با `asyncio.to_thread` اجرا می‌شوند تا event loop بلاک نشود.
- خواندن با `aiofiles` انجام می‌شود.

---

## ۳. کلاس عمومی `JsonCollection[ModelT]`

یک سند JSON همگن را مدیریت می‌کند و generic روی مدل Pydantic است.

### قابلیت‌ها

| متد | کار |
|-----|-----|
| `read_all()` | خواندن و اعتبارسنجی همه رکوردها |
| `write_all(items)` | جایگزینی کل سند به‌صورت اتمیک |
| `update(mutator)` | چرخه کامل read-modify-write تحت قفل |
| `find_one(id, key="id")` | یافتن رکورد |
| `add(item)` | افزودن |
| `upsert(item)` | درج یا جایگزینی |
| `remove(id)` | حذف (با بازگشت `bool`) |
| `clear()` | خالی کردن سند |
| `ensure_document()` | ساخت فایل با آرایه خالی در صورت نبود |

### قفل‌گذاری
هر collection یک `asyncio.Lock` اختصاصی دارد. `update()` کل چرخه خواندن→تغییر→نوشتن را داخل قفل اجرا می‌کند، بنابراین دو درخواست همزمان (مثلاً ثبت کد توسط ربات و ویرایش توسط ادمین) باعث lost update نمی‌شوند.

### اعتبارسنجی
- JSON نامعتبر → `CorruptDatabaseError` (شامل مسیر فایل و دلیل)
- ساختار غیرآرایه → `CorruptDatabaseError`
- رکورد ناسازگار با مدل → `DatabaseValidationError` (شامل `index` هر رکورد و جزئیات Pydantic)
- حالت اختیاری `quarantine_on_corruption=True` → فایل خراب به `*.corrupt-<UTC timestamp>.json` تغییر نام می‌دهد و مجموعه خالی ادامه می‌دهد (پیش‌فرض: خاموش، تا داده بی‌صدا از دست نرود)

### خطاهای دامنه
```
StorageError
├── CorruptDatabaseError
├── DatabaseValidationError
├── RecordNotFoundError
└── DuplicateEntryCodeError
```

---

## ۴. کلاس `StorageManager` (Facade)

سه collection را به مدل‌های مشخص متصل می‌کند و API سطح‌بالا می‌دهد.

### راه‌اندازی
```python
storage = StorageManager()          # پیش‌فرض: TourneyCore/data
await storage.initialize()          # ساخت دایرکتوری و فایل‌های خالی
```

### سیستم‌های بازی
`list_systems`, `get_system`, `require_system`, `create_system(name, description, max_players)`, `save_system`, `delete_system`

### بازیکنان (سناریوی A)
- `list_players(system_id=None)` — فیلتر بر اساس تورنمنت
- `get_player` / `require_player`
- `find_player_by_entry_code(code)` — نرمال‌سازی خودکار (uppercase، حذف `-` و فاصله)
- `create_player(system_id, name, entry_code=None)`
  - کد ورود یکتا **داخل قفل** تضمین می‌شود
  - در صورت تولید خودکار، تا ۱۰ بار تلاش برای کد بدون تصادم
  - کد تکراری صریح → `DuplicateEntryCodeError`
- `redeem_entry_code(code, telegram_id, username=None)` — عملیات اصلی چک‑این:
  - کد ناموجود → `RecordNotFoundError`
  - کد قبلاً استفاده‌شده → `StorageError`
  - اتصال حساب تلگرام تکراری → `StorageError`
  - در موفقیت، `Player.link_telegram()` وضعیت را به `registered` می‌برد
- `save_player` / `delete_player`

### مسابقات
`list_matches(system_id=None)`, `get_match`, `require_match`, `save_match`, `save_matches(bulk)`, `delete_match`, `delete_matches_for_system`

### خروجی برای فرانت‌اند
`export_snapshot()` → دیکشنری JSON-سازگار:
```json
{ "systems": [...], "players": [...], "matches": [...] }
```

---

## ۵. تست دودی (Smoke Test)

با یک دایرکتوری موقت اجرا شد:

| سناریو | نتیجه |
|--------|-------|
| `initialize()` ساخت هر سه فایل | ✅ `systems.json`, `users.json`, `matches.json` |
| `create_system` + `create_player` | ✅ کد ورود ۸ کاراکتری تولید شد |
| `redeem_entry_code` با حروف کوچک | ✅ نرمال‌سازی شد و وضعیت `registered` شد |
| استفاده دوباره از همان کد | ✅ `StorageError` |
| ساخت بازیکن با کد تکراری | ✅ `DuplicateEntryCodeError` |
| `export_snapshot` | ✅ `{systems:1, players:1, matches:0}` |
| فایل خراب (`{not json`) | ✅ `CorruptDatabaseError` |

همچنین تست چرخه `add → upsert → remove` روی `JsonCollection` و بررسی محتوای روی دیسک انجام شد.

---

## ۶. تاریخچه کامیت‌ها (روی `feature/phase-3`)

| Commit | پیام |
|--------|------|
| `e5ac5cf` | `feat: add storage errors and atomic async JSON file helpers` |
| `73170df` | `feat: add generic async JSON collection with locking and validation` |
| `97491d6` | `feat: add StorageManager facade for systems, players and matches` |
| `e56c8e6` | `feat: export storage API from the core package` |
| `f1fa1f1` | `chore: ignore the runtime JSON data directory` |
| `docs: add phase 3 Persian report` | همین گزارش |

---

## ۷. آمادگی برای فاز ۴

لایه‌ی ذخیره‌سازی اکنون آماده است تا `core/bracket.py`:

- براکت حذفی دوگانه را بسازد و با `save_matches()` یک‌جا ذخیره کند،
- پس از هر `win` / `walkover`، مسابقات پیش‌رو و بعدی را به‌روز کند و با `save_match()` بنویسد،
- بازیکنان حذف‌شده را با `save_player()` به‌روز کند.

**وضعیت:** آماده فاز ۴ — Double Elimination Logic
