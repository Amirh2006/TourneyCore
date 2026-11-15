# TourneyCore

A small, framework-independent tournament manager for **double-elimination**
events, with a Telegram bot, an admin CLI and a live web dashboard (Telegram
Mini-App) on top of a plain JSON database.

Tournament rules live in `core/` only. The CLI, the bot and the web API are
thin presentation layers, so the same data can be driven from any of them.

> **Serverless migration:** [`worker/`](worker/README.md) contains the
> Cloudflare Workers + Hono implementation. It replaces long polling with
> `POST /webhook`, reads the existing Supabase schema, and serves the REST API
> used by the mini-app. The Python implementation below stays available for
> local development until the cut-over is complete.

## Features

- Double elimination with winners bracket, losers bracket and a grand final
  (including the reset final).
- Any roster size from 2 upwards — non-power-of-two fields are padded with
  byes awarded to the top seeds.
- Atomic, validated JSON persistence (`aiofiles` + Pydantic v2).
- Admin CLI: create a tournament, add players, print entry codes, seed the
  bracket and record results (with optional Telegram notifications).
- Telegram bot: entry-code registration, `/me`, `/next`, `/bracket`,
  `/standings`, and a button that opens the mini-app.
- Read-only web dashboard: live bracket, standings and champion, with
  Server-Sent Events push updates, a dark/light theme and Telegram WebApp
  integration.
- 68 automated tests.

## Requirements

- Python 3.12+
- The dependencies in `requirements.txt` (`pydantic`, `aiofiles`, `aiogram`).
- No database server, no build step, no frontend toolchain.

## Quick start

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
python -m web.server --port 8080      # http://127.0.0.1:8080
```

Run the bot with:

```powershell
$env:TOURNEYCORE_BOT_TOKEN='123456:ABC...'
$env:TOURNEYCORE_WEBAPP_URL='https://tourney.example.com/'   # optional
python -m bot.main
```

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `TOURNEYCORE_BOT_TOKEN` | bot, CLI `--notify` | Telegram bot token (required for the bot) |
| `TOURNEYCORE_DATA_DIR` | bot | Override the JSON data directory |
| `TOURNEYCORE_WEBAPP_URL` | bot | Public **HTTPS** URL of the mini-app; enables `/app` and the menu button |

The token is only read from the environment; it is never written to disk or
committed.

## CLI commands

| Command | Purpose |
|---------|---------|
| `init NAME [--max-players N] [--description TEXT]` | Create a tournament and open registration |
| `add-player SYSTEM_ID NAME` | Add a player and print their entry code |
| `register CODE --telegram-id ID [--username NAME]` | Redeem an entry code |
| `list-systems` | List tournaments and their status |
| `list-players SYSTEM_ID` | List players, codes and records |
| `seed SYSTEM_ID` | Generate the double-elimination bracket |
| `start MATCH_ID` | Mark a READY match as in progress |
| `win MATCH_ID PLAYER_ID [--score1 A --score2 B] [--notify]` | Record a result |
| `walkover MATCH_ID PLAYER_ID [--notify]` | Record a no-show win |
| `list-matches SYSTEM_ID` / `show-bracket SYSTEM_ID` | Print the bracket |

`--notify` sends Telegram messages to players whose next match just became
READY. Without a token it prints a warning and still records the result.

## Web API

The server is read-only by design — results are recorded by the CLI.

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Liveness probe |
| `GET /api/systems` | Tournaments and aggregate counts |
| `GET /api/systems/<id>` | Dashboard: system, players, brackets, champion |
| `GET /api/stream` | Server-Sent Events stream (`update` on every data change) |
| `GET /` | The static mini-app |

`entry_code` and `telegram_id` are never included in API responses. Mutating
HTTP verbs return `405 Method Not Allowed`.

## Project layout

```
core/        models, atomic JSON storage, bracket engine (no I/O in models)
cli/         admin command line interface
bot/         aiogram handlers, notifications and configuration
web/         read-only JSON API, SSE stream and static file server
frontend/    mini-app HTML/CSS/JS (no build step)
tests/       unittest suite (bracket, CLI, bot, web)
```

## Testing

```powershell
$env:PYTHONPATH='D:\Projects\TourneyCore'
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

CI runs the same suite on every push and pull request (`.github/workflows/tests.yml`).

## Notes

- The web server binds to `127.0.0.1` by default. Use `--host 0.0.0.0` to show
  the dashboard on the venue network.
- Telegram only opens HTTPS mini-app URLs; use a TLS-terminating reverse proxy
  (or a tunnel) in front of `web.server`.
- Phase reports (`PHASE_1_REPORT.md` … `PHASE_8_REPORT.md`) document each
  development phase in Persian.
