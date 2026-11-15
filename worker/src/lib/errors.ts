import type { ContentfulStatusCode } from 'hono/utils/http-status';

/**
 * An error that is safe to expose to an HTTP caller.
 *
 * `code` is a stable machine-readable identifier (`not_found`,
 * `database_error`, ...) while `message` is a human-readable detail. The
 * global Hono error handler turns these into JSON responses.
 */
export class ApiError extends Error {
  readonly status: ContentfulStatusCode;
  readonly code: string;

  constructor(status: ContentfulStatusCode, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

/**
 * A user-facing bot failure (unknown entry code, already redeemed, ...).
 *
 * The webhook catches these and replies with the message instead of failing
 * the HTTP request, because Telegram would otherwise retry the same update.
 */
export class BotError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BotError';
  }
}
