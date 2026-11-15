# TourneyCore Worker

Serverless replacement for the Python bot + web server:

- **Cloudflare Workers** (TypeScript) with **Hono** for routing.
- **Telegram webhook** at `POST /webhook` — no long polling.
- **Read-only REST API** for the mini-app — no SSE, the client polls.
- **Supabase** (Postgres) stays the database of record; the schema is unchanged.
- Strict CORS: every response is readable from the Cloudflare Pages origin, and
  `OPTIONS` preflights answer `204`.

```
src/
  index.ts              Hono app, CORS, logging, error handling
  env.ts                Worker bindings
  types.ts              Supabase rows + public API payloads
  lib/
    supabase.ts         Edge client, pagination, single-row reads
    bracket.ts          is_match_dead (parity with core/bracket.py)
    telegram.ts         Bot API client + Update types
    errors.ts           ApiError / BotError
  routes/
    webhook.ts          POST /webhook and all bot commands
    api.ts              GET /api/health, /api/systems, /api/systems/:id
  services/
    queries.ts          Typed Supabase reads and aggregate counts
    tournaments.ts      Dashboard and tournament-selector builders
    players.ts          Entry-code redemption
    bot-format.ts       Profile, next match, bracket and standings text
scripts/
  set-webhook.mjs       Registers the deployed Worker with Telegram
```

## 1. Install

```powershell
cd D:\Projects\TourneyCore\worker
npm install
```

Requires Node.js 20 or newer.

## 2. Configure secrets

```powershell
npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_SERVICE_ROLE_KEY
```

Optional but strongly recommended — protects `/webhook` from forged updates:

```powershell
# Generate a value once and keep it somewhere safe.
openssl rand -hex 32
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
```

`SUPABASE_SERVICE_ROLE_KEY` must never reach the browser.

## 3. Local development

```powershell
Copy-Item .dev.vars.example .dev.vars
# Fill in the three required values.
npm run dev
# http://127.0.0.1:8787
```

Useful local checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/health
Invoke-RestMethod http://127.0.0.1:8787/api/systems
```

To receive real Telegram updates locally, expose the dev server with a tunnel
and point the webhook at it:

```powershell
# Example with cloudflared:
cloudflared tunnel --url http://127.0.0.1:8787
# Then set the webhook to https://<tunnel-host>/webhook
```

## 4. Deploy

```powershell
npm run typecheck
npx wrangler deploy
```

The Worker is published at
`https://tourneycore.<your-subdomain>.workers.dev`.

If the mini-app is served from a different origin, add its URL as a normal
variable so `/app` can open it:

```toml
# wrangler.toml
[vars]
MINI_APP_URL = "https://tourneycore.pages.dev"
```

Then deploy again.

## 5. Point Telegram at the Worker

```powershell
$env:TELEGRAM_BOT_TOKEN = '123456:ABC...'
$env:WORKER_URL = 'https://tourneycore.<your-subdomain>.workers.dev'
$env:TELEGRAM_WEBHOOK_SECRET = '<the same value you stored above>'
npm run webhook:set
```

Verify:

```powershell
Invoke-RestMethod "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

`pending_update_count` should stay at `0` and `last_error_message` should be
absent.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/systems` | Tournaments with player/match/ready counts |
| `GET` | `/api/systems/:id` | Full dashboard: system, players, brackets, champion |
| `POST` | `/webhook` | Telegram updates (optionally secret-token protected) |

Responses use `Cache-Control: no-store`. Unknown routes and tournaments return
JSON `{ "error": "...", "detail": "..." }` rather than an HTML error page.

## Frontend

`frontend/app.js` reads `window.TOURNEYCORE_API_BASE` or the
`<meta name="tourneycore-api" content="...">` tag. Set it to the Worker origin
when the mini-app is hosted on Cloudflare Pages:

```html
<meta name="tourneycore-api" content="https://tourneycore.example.workers.dev" />
```

Leave it empty when the API and the page share an origin. The page polls
`/api/systems/:id` every 10 seconds (configurable with `?refresh=5000`) and
skips refreshes while the tab is hidden. SSE is no longer used.

## Notes

- Results are still recorded by the Python admin CLI; this Worker is a
  read-only projection plus the player-facing bot commands.
- `lib/bracket.ts` is a line-for-line port of `is_match_dead` and was checked
  against the Python engine for roster sizes 2–13.
- Supabase requests are paginated in 1,000-row batches so a long tournament
  history never silently truncates counts.
