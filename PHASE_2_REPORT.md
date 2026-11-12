# گزارش فاز ۲: مدل‌های داده اصلی (Core Data Models)

## هدف فاز
تعریف سه موجودیت اصلی دامنه با استفاده از **Pydantic v2** به‌گونه‌ای که:

- کاملاً type-safe و دارای اعتبارسنجی (validation) باشند،
- به‌صورت مستقیم و بدون تبدیل دستی به JSON سریالایز/دی‌سریالایز شوند (آماده برای `aiofiles` در فاز ۳)،
- هیچ عملیات I/O یا منطق براکت داخلشان نباشد (جداسازی مسئولیت‌ها).

فایل اصلی: `core/models.py` — فایل بسته: `core/__init__.py` — نشانگر تایپ: `core/py.typed`

---

## ۱. Enumهای دامنه

| Enum | مقادیر | کاربرد |
|------|--------|--------|
| `PlayerStatus` | `pending`, `registered`, `active`, `eliminated`, `withdrawn` | چرخه حیات بازیکن |
| `MatchStatus` | `pending`, `ready`, `active`, `completed` | چرخه حیات مسابقه |
| `BracketSide` | `winners`, `losers`, `grand_final` | نوع براکت در حذفی دوگانه |
| `WinMethod` | `normal`, `walkover` | نحوه تعیین نتیجه (پیروزی عادی / انصراف یا عدم حضور) |
| `SystemStatus` | `draft`, `registration`, `running`, `completed` | چرخه حیات سیستم بازی |

استفاده از `str, Enum` باعث می‌شود مقادیر در JSON به‌صورت رشته خوانا ذخیره شوند (`"registered"` به‌جای عدد).

---

## ۲. کلاس پایه `CoreModel`

پیکربندی مشترک همه مدل‌ها:

```python
model_config = ConfigDict(
    populate_by_name=True,
    validate_assignment=True,
    extra="forbid",
    str_strip_whitespace=True,
)
```

- `extra="forbid"` → کلید ناشناخته در JSON خطا می‌دهد؛ مانع drift شدن شِما می‌شود.
- `validate_assignment=True` → هر تغییر فیلد دوباره اعتبارسنجی می‌شود؛ موجودیت هرگز در وضعیت نامعتبر نمی‌ماند.
- `str_strip_whitespace=True` → نام‌ها و توضیحات قبل از اعتبارسنجی trim می‌شوند.
- متد `utc_now()` زمان timezone-aware UTC برمی‌گرداند (بدون naive datetime).

همچنین دو تابع کمکی:

- `generate_model_id(prefix)` → شناسه‌هایی مثل `plr_1a2b3c4d5e6f`
- `generate_entry_code(length=8)` → کد ورود تصادفی از الفبای بدون کاراکترهای گیج‌کننده (`ABCDEFGHJKLMNPQRSTUVWXYZ23456789`، بدون `O/0/I/1`)

---

## ۳. مدل `GamingSystem`

نماینده یک تورنمنت/بازی (مثلاً «Tekken 8 — Friday Cup»).

**فیلدها:** `id`, `name`, `description`, `max_players` (۲ تا ۵۱۲), `status`, `registration_open`, `player_ids`, `created_at`, `updated_at`

**قواعد اعتبارسنجی:**

- نام نمی‌تواند فقط فاصله باشد.
- `player_ids` تکراری مجاز نیست.
- تعداد بازیکنان نمی‌تواند از `max_players` بیشتر شود (model-level validator).

**رفتارها:**

- `player_count` / `is_full` / `can_start` (حداقل ۲ بازیکن و وضعیت `registration`)
- `add_player()` با بررسی ظرفیت و تکراری بودن + به‌روزرسانی `updated_at`
- `remove_player()`

---

## ۴. مدل `Player` (سناریوی A)

بازیکن ابتدا با یک **کد ورود** توسط ادمین ساخته می‌شود و بعداً در ربات تلگرام کد را وارد می‌کند تا `telegram_id` به رکورد متصل شود.

**فیلدها:** `id`, `system_id`, `name`, `entry_code`, `telegram_id`, `telegram_username`, `status`, `seed`, `wins`, `losses`, `created_at`, `registered_at`, `eliminated_at`

**قواعد:**

- `entry_code` به فرم کانونیکال تبدیل می‌شود: uppercase + حذف `-` و فاصله (طول ۶ تا ۱۰، فقط حروف/ارقام).
- `telegram_username` بدون `@` ذخیره می‌شود.
- سازگاری چرخه حیات: وضعیت `registered`/`active` الزاماً `telegram_id` و `registered_at` دارد؛ وضعیت `eliminated` الزاماً `eliminated_at` دارد.

**رفتارها:**

- `link_telegram(telegram_id, username)` → اتصال حساب و انتقال به `registered` (اگر قبلاً متصل شده باشد خطا می‌دهد).
- `mark_active()`
- `register_win()`
- `register_loss()` → در باخت دوم، `eliminated_at` ست شده و وضعیت `eliminated` می‌شود و `True` برمی‌گرداند.
- `withdraw()`
- پراپرتی‌های `is_registered`, `is_eliminated`, `can_play`

---

## ۵. مدل `Match`

هر مسابقه در حذفی دوگانه، علاوه بر بازیکنان و نتیجه، **مسیر پیشروی** را صریح نگه می‌دارد:

- `next_winner_match_id` → مسابقه‌ای که برنده به آن صعود می‌کند.
- `next_loser_match_id` → مسابقه‌ای که بازنده به آن می‌افتد (`None` در فینال بزرگ = حذف).

**فیلدها:** `id`, `system_id`, `bracket`, `round`, `position`, `player1_id`, `player2_id`, `winner_id`, `loser_id`, `status`, `win_method`, `score_player1`, `score_player2`, `next_winner_match_id`, `next_loser_match_id`, `is_bye`, `created_at`, `started_at`, `completed_at`

**قواعد یکپارچگی (model validator):**

- یک بازیکن نمی‌تواند حریف خودش شود.
- `winner_id` و `loser_id` باید از بین دو بازیکن باشند و با هم متفاوت باشند.
- وضعیت `ready` نیازمند پر بودن هر دو جایگاه است.
- وضعیت `active` نیازمند هر دو بازیکن + `started_at` است.
- وضعیت `completed` نیازمند برنده و `completed_at` است و در مسابقه غیر‑BYE نیازمند `win_method` است.

**رفتارها:**

- `assign_players(p1, p2)` → پر کردن جایگاه‌ها و تنظیم خودکار `ready`/`pending`
- `start()` → فقط از وضعیت `ready`، ثبت `started_at`
- `complete(winner_id, method, score1, score2)` → ثبت بازنده/برنده/امتیاز/زمان و انتقال به `completed`
- پراپرتی‌های `has_both_players`, `is_ready`, `is_completed`
- `other_player(player_id)`

> نکته: منطق **پیشروی براکت** (تعیین `next_*` و انتقال خودکار برنده/بازنده) عمداً در این فاز پیاده نشده و به `core/bracket.py` در فاز ۴ سپرده شده است؛ این فایل فقط داده و قواعد سازگاری را نگه می‌دارد.

---

## ۶. تست دودی (Smoke Test)

- ساخت `GamingSystem` و افزودن بازیکن ✅
- رد شدن نام خالی ✅
- تبدیل کد ورود `" ab-12cd34 "` به `AB12CD34` ✅
- اتصال تلگرام و انتقال وضعیت ✅
- حذف بازیکن پس از باخت دوم ✅
- چرخه کامل `pending → ready → active → completed` برای `Match` ✅
- جلوگیری از بازی بازیکن با خودش ✅
- **Round-trip کامل JSON:** `model_dump(mode="json")` → `model_validate(...)` برای هر سه مدل برابر با نمونه اصلی ✅

---

## ۷. تاریخچه کامیت‌ها (روی `feature/phase-2`)

| Commit | پیام |
|--------|------|
| `aa0e226` | `feat: add core enums, shared model config and entry code generator` |
| `bbf53b3` | `feat: add GamingSystem model with roster capacity rules` |
| `5d7ff2a` | `feat: add Player model with entry code redemption and elimination tracking` |
| `af050d3` | `feat: add Match model with bracket linkage and result invariants` |
| `3ae92d1` | `feat: expose core package public API and type marker` |
| `docs: add phase 2 Persian report` | همین گزارش |

---

## ۸. آمادگی برای فاز ۳

مدل‌ها با `model_dump(mode="json")` خروجی کاملاً JSON-سازگار می‌دهند (datetime → ISO-8601، enum → رشته). بنابراین فاز ۳ می‌تواند `core/storage.py` را با `aiofiles` و نوشتن اتمیک (فایل موقت + `os.replace`) روی `users.json`، `matches.json` و `systems.json` بسازد.

**وضعیت:** آماده فاز ۳ — Async Storage Manager
