<div align="center">

# 🏆 TourneyCore

**A Modern, Framework-Independent Double-Elimination Tournament Manager**  
*Telegram Bot • Interactive RTL Telegram Mini-App • Admin CLI • Serverless Edge API*

[![Version](https://img.shields.io/badge/version-1.5.2-7B2CBF?style=for-the-badge)](https://github.com/Amirh2006/TourneyCore/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-00F5D4?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers%20%26%20Pages-F38020?style=for-the-badge&logo=cloudflare&logoColor=white)](https://workers.cloudflare.com/)
[![Hono](https://img.shields.io/badge/Hono-Edge%20API-E36002?style=for-the-badge&logo=hono&logoColor=white)](https://hono.dev/)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com/)

</div>

---

## 📖 Overview

**TourneyCore** is a small, framework-independent tournament manager for **double-elimination** events, featuring a **Telegram Bot**, an **Admin CLI**, and a live **Web Dashboard (Telegram Mini-App)** on top of a plain JSON database.

Tournament rules live in `core/` only. The CLI, the bot, and the web API are thin presentation layers, so the same data can be driven seamlessly from any of them.

> **⚡ Serverless Migration (`v1.5.2`):** [`worker/`](worker/README.md) contains the production **Cloudflare Workers + Hono** implementation. It replaces long polling with `POST /webhook`, reads and updates the **Supabase (PostgreSQL)** schema, and serves a secure, role-based REST API for the interactive Persian/RTL Mini-App deployed on **Cloudflare Pages**. The Python implementation below remains available for local development until the cut-over is complete.

---

## ✨ Features

### Core & Bracket Engine
- **Double Elimination:** Full support for winners bracket, losers bracket, and grand final (including the reset final).
- **Flexible Roster Sizes:** Supports any roster size from 2 upwards — non-power-of-two fields are automatically padded with byes awarded to the top seeds.
- **68 Automated Tests:** Comprehensive `unittest` suite covering bracket logic, CLI, bot, and web layers, executed automatically via GitHub Actions CI.

### Serverless Production Stack (Cloudflare Workers + Supabase + Mini-App)
- **Full Persian Localization & RTL Layout (`v1.5.2`):** Native right-to-left (`dir="rtl"`) Mini-App interface styled with the `Vazirmatn` typeface, Cyber-Violet dark theme & Yellow light theme, mirrored 90-degree bracket tree connectors for RTL progression, and fully localized Persian Telegram bot commands, responses, and inline buttons.
- **Role-Based Access Control (RBAC):** Supports `admin`, `coworker`, `player`, and `guest` roles backed by cryptographically verified Telegram Mini-App authentication.
- **Interactive In-App Management:** Create and delete tournaments, register players, seed/start brackets, record match scores directly from bracket cards, and promote coworkers.
- **In-App Announcement Banner & History:** Neon Cyber-Violet/Gold broadcast banner with a scrollable history log and formatted Jalali (`fa-IR`) date/time stamps.
- **Username-Based Auto-Linking:** Admins register players using their Telegram `@username` instead of numeric IDs. When a player opens the Mini-App (`auth.ts`) or messages the bot (`webhook.ts`), their numeric `telegram_id` is silently linked in Supabase.
- **Match Notifications with Delivery Reports:** Per-match alert button (`🔔`) sends Telegram notifications with an inline Web App button to launch the live bracket and returns an exact per-player delivery report (sent, not started, blocked, or missing username).

### Local Python Stack (CLI + Bot + Read-Only Dashboard)
- **Atomic, Validated JSON Persistence:** Powered by `aiofiles` + Pydantic v2 — no database server required.
- **Admin CLI:** Create tournaments, add players, print one-time entry codes, seed brackets, and record results (with optional Telegram notifications).
- **Telegram Bot:** Entry-code registration (`/register`), `/start`, `/help`, `/app`, `/me`, `/next`, `/bracket`, `/standings`, and a button that opens the Mini-App.
- **Read-Only Web Dashboard:** Live bracket, standings, and champion view with Server-Sent Events (SSE) push updates, dark/light theme support, and Telegram WebApp integration.

---

## 🛡️ Security

- **Cryptographic Mini-App Auth (`HMAC-SHA256`):** `authMiddleware` validates `X-Telegram-Init-Data` via Web Crypto (`crypto.subtle`) using Telegram's `WebAppData` key derivation and constant-time hash comparison, failing closed to `guest` on invalid signatures.
- **Replay Attack Mitigation:** Rejects `initData` payloads older than 24 hours (`auth_date` validation).
- **Webhook Secret Verification:** Validates `X-Telegram-Bot-Api-Secret-Token` using constant-time string comparison (`safeEqual`).
- **XSS-Hardened Frontend:** Renders dynamic tournament data, announcements, and bracket cards strictly via safe DOM APIs (`createElement`, `textContent`) and `addEventListener`, eliminating `innerHTML` and inline event handlers.
- **Secret Isolation:** Bot tokens and database credentials are read exclusively from environment variables/secrets and are never written to disk or committed.

---

## 🛠 Requirements

### Local Python Mode
- **Python 3.12+**
- Dependencies listed in `requirements.txt` (`pydantic`, `aiofiles`, `aiogram`).
- No database server, no build step, and no frontend toolchain.

### Serverless Mode
- **Node.js 18+** and **Cloudflare Wrangler CLI** (`npx wrangler`)
- **Supabase** project (PostgreSQL)
- **Cloudflare Workers** (API & Webhook) and **Cloudflare Pages** (Frontend Mini-App)

---

## 🚀 Quick Start (Local Python Mode)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. Create a tournament and open registration
python -m cli.admin init "Friday Cup" --max-players 8

# 2. Add players (each one gets a one-time entry code)
python -m cli.admin add-player sys_... Alice
python -m cli.admin add-player sys_... Bob

# 3. Players redeem their codes in the Telegram bot, or an admin can do it:
python -m cli.admin register ABCD2345 --telegram-id 123456789

# 4. Seed the bracket and play
python -m cli.admin seed sys_...
python -m cli.admin list-matches sys_...
python -m cli.admin win m_... plr_... --score1 3 --score2 1 --notify

# 5. Watch it live
python -m web.server --port 8080      # [http://127.0.0.1:8080](http://127.0.0.1:8080)
```

Run the bot with:

```powershell
$env:TOURNEYCORE_BOT_TOKEN='123456:ABC...'
$env:TOURNEYCORE_WEBAPP_URL='[https://tourney.example.com/](https://tourney.example.com/)'   # optional
python -m bot.main
```

---

## ☁️ Serverless Deployment (Cloudflare + Supabase)

### 1. Supabase Schema Updates (`v1.4.0` – `v1.5.2`)
Run the following SQL in your Supabase SQL Editor to ensure the announcement and username columns exist and refresh the PostgREST schema cache:

```sql
ALTER TABLE systems ADD COLUMN IF NOT EXISTS announcement TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS telegram_username TEXT;
NOTIFY pgrst, 'reload schema';
```

### 2. Deploy Backend & Frontend

```bash
# Deploy Cloudflare Worker (Backend API & Telegram Webhook)
cd worker
npx wrangler deploy

# Deploy Cloudflare Pages (Frontend Mini-App)
cd ..
npx wrangler pages deploy . --branch main
```

---

## 🔐 Environment Variables

### Local Python Mode

| Variable | Used by | Purpose |
|----------|---------|---------|
| `TOURNEYCORE_BOT_TOKEN` | bot, CLI `--notify` | Telegram bot token (required for the bot) |
| `TOURNEYCORE_DATA_DIR` | bot | Override the JSON data directory |
| `TOURNEYCORE_WEBAPP_URL` | bot | Public **HTTPS** URL of the mini-app; enables `/app` and the menu button |

### Serverless Worker Mode (`worker/`)

| Variable / Secret | Purpose |
|-------------------|---------|
| `SUPABASE_URL` | Supabase project REST API URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key for server-side database operations |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token (also used for `initData` HMAC verification) |
| `TELEGRAM_WEBHOOK_SECRET` | Secret token used to verify `X-Telegram-Bot-Api-Secret-Token` |
| `MINI_APP_URL` | Public **HTTPS** URL of the Mini-App (enables inline bracket launch buttons) |

---

## 💻 CLI Commands (Local Mode)

| Command | Purpose |
|---------|---------|
| `init NAME [--max-players N] [--description TEXT]` | Create a tournament and open registration |
| `add-player SYSTEM_ID NAME` | Add a player and print their entry code |
| `register CODE --telegram-id ID [--username NAME]` | Redeem an entry code |
| `list-systems` | List tournaments and their status |
| `list-players SYSTEM_ID` | List players, codes, and records |
| `seed SYSTEM_ID` | Generate the double-elimination bracket |
| `start MATCH_ID` | Mark a READY match as in progress |
| `win MATCH_ID PLAYER_ID [--score1 A --score2 B] [--notify]` | Record a result |
| `walkover MATCH_ID PLAYER_ID [--notify]` | Record a no-show win |
| `list-matches SYSTEM_ID` / `show-bracket SYSTEM_ID` | Print the bracket |

`--notify` sends Telegram messages to players whose next match just became `READY`. Without a token, it prints a warning and still records the result.

---

## 🌐 Web API

### Local Python Read-Only API
The local server is read-only by design — results are recorded by the CLI. `entry_code` and `telegram_id` are never included in API responses, and mutating HTTP verbs return `405 Method Not Allowed`.

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Liveness probe |
| `GET /api/systems` | Tournaments and aggregate counts |
| `GET /api/systems/<id>` | Dashboard: system, players, brackets, champion |
| `GET /api/stream` | Server-Sent Events stream (`update` on every data change) |
| `GET /` | The static mini-app |

### Serverless Worker API (`worker/`)

| Endpoint | Access | Description |
|----------|--------|-------------|
| `GET /api/health` | Public | Liveness probe |
| `GET /api/systems` | Public | List tournaments and verified user role |
| `GET /api/systems/:id` | Public | Full tournament dashboard (standings, brackets, announcements) |
| `POST /api/systems` | Coworker / Admin | Create a new tournament |
| `DELETE /api/systems/:id` | Admin | Permanently delete a tournament |
| `POST /api/systems/:id/players` | Coworker / Admin | Register a player (with optional `telegram_username`) |
| `POST /api/systems/:id/start` | Coworker / Admin | Close registration and seed the bracket |
| `PUT /api/matches/:id` | Coworker / Admin | Record match scores and advance the bracket |
| `POST /api/matches/:id/notify` | Coworker / Admin | Send Telegram match alert with delivery report & WebApp button |
| `POST /api/systems/:id/broadcast` | Coworker / Admin | Append to (or clear) the tournament announcement history |
| `POST /api/roles/promote` | Admin | Promote a Telegram user to `coworker` |
| `POST /webhook` | Telegram | Telegram Bot webhook entry point |

---

## 📂 Project Layout

```text
core/        models, atomic JSON storage, bracket engine (no I/O in models)
cli/         admin command line interface
bot/         aiogram handlers, notifications and configuration
web/         read-only JSON API, SSE stream and static file server
worker/      Cloudflare Workers + Hono serverless API & Telegram webhook
frontend/    RTL Persian mini-app HTML/CSS/JS (no build step)
tests/       unittest suite (bracket, CLI, bot, web)
```

---

## 🧪 Testing

```powershell
$env:PYTHONPATH='D:\Projects\TourneyCore'
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

CI runs the same suite on every push and pull request (`.github/workflows/tests.yml`).

---

## 📌 Notes

- The local web server binds to `127.0.0.1` by default. Use `--host 0.0.0.0` to show the dashboard on the venue network.
- Telegram only opens **HTTPS** mini-app URLs; use a TLS-terminating reverse proxy (or a tunnel) in front of `web.server`, or deploy via Cloudflare Pages.
- Phase reports (`PHASE_1_REPORT.md` … `PHASE_8_REPORT.md`) document each development phase in Persian.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.