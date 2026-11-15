/**
 * Runtime bindings for the TourneyCore Worker.
 *
 * Secrets are configured with `wrangler secret put`; non-secret values live in
 * `[vars]` inside `wrangler.toml`. Keeping this interface in one place makes
 * missing configuration a compile-time error instead of a production surprise.
 */
export interface Env {
  /** Telegram Bot API token issued by @BotFather. Secret. */
  TELEGRAM_BOT_TOKEN: string;

  /** Supabase project URL, for example `https://abcdefgh.supabase.co`. */
  SUPABASE_URL: string;

  /** Supabase service-role key. Secret: it bypasses row level security. */
  SUPABASE_SERVICE_ROLE_KEY: string;

  /**
   * Optional shared secret registered with `setWebhook`. When set, every
   * update must carry the matching `X-Telegram-Bot-Api-Secret-Token` header.
   */
  TELEGRAM_WEBHOOK_SECRET?: string;

  /** Optional public HTTPS address of the mini-app, used by the `/app` command. */
  MINI_APP_URL?: string;
}
