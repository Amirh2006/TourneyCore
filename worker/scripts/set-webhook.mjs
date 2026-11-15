#!/usr/bin/env node
/**
 * Register the deployed Worker as the Telegram webhook.
 *
 * Usage (PowerShell):
 *   $env:TELEGRAM_BOT_TOKEN = '123456:ABC...'
 *   $env:WORKER_URL = 'https://tourneycore.<subdomain>.workers.dev'
 *   $env:TELEGRAM_WEBHOOK_SECRET = '...'   # optional but recommended
 *   node scripts/set-webhook.mjs
 */

const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
const baseUrl = process.env.WORKER_URL?.trim();
const secret = process.env.TELEGRAM_WEBHOOK_SECRET?.trim();

if (!token || !baseUrl) {
  console.error(
    'Usage: TELEGRAM_BOT_TOKEN=... WORKER_URL=https://... node scripts/set-webhook.mjs',
  );
  process.exit(1);
}

const webhookUrl = `${baseUrl.replace(/\/+$/, '')}/webhook`;
const payload = {
  url: webhookUrl,
  allowed_updates: ['message'],
  drop_pending_updates: true,
};

if (secret) {
  payload.secret_token = secret;
}

const response = await fetch(
  `https://api.telegram.org/bot${token}/setWebhook`,
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  },
);

const body = await response.json().catch(() => null);
console.log(JSON.stringify({ webhook: webhookUrl, response: body }, null, 2));
process.exit(response.ok && body?.ok ? 0 : 1);
