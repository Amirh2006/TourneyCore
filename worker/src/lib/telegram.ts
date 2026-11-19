import { ApiError } from './errors';

/** Minimal Telegram Bot API types used by the webhook. */
export interface TelegramUser {
  id: number;
  is_bot: boolean;
  first_name: string;
  username?: string;
}

export interface TelegramChat {
  id: number;
  type: string;
}

export interface TelegramMessage {
  message_id: number;
  from?: TelegramUser;
  chat: TelegramChat;
  date: number;
  text?: string;
}

export interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
}

export interface InlineKeyboardButton {
  text: string;
  web_app?: { url: string };
}

export interface InlineKeyboardMarkup {
  inline_keyboard: InlineKeyboardButton[][];
}

const API_BASE = 'https://api.telegram.org';

/** Telegram rejects messages longer than 4096 characters. */
const MAX_MESSAGE_LENGTH = 3_900;

interface TelegramEnvelope<T> {
  ok?: boolean;
  result?: T;
  description?: string;
  error_code?: number;
}

/** A non-2xx response from the Bot API. */
class TelegramError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'TelegramError';
    this.status = status;
  }
}

export class TelegramClient {
  constructor(private readonly token: string) {}

  private async call<T>(method: string, payload: unknown): Promise<T> {
    if (!this.token) {
      throw new ApiError(
        500,
        'configuration_error',
        'TELEGRAM_BOT_TOKEN is not configured.',
      );
    }

    const response = await fetch(`${API_BASE}/bot${this.token}/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10_000),
    });

    const envelope = (await response
      .json()
      .catch(() => null)) as TelegramEnvelope<T> | null;

    if (!response.ok || envelope?.ok !== true) {
      const description = envelope?.description ?? response.statusText;
      throw new TelegramError(
        response.status,
        `Telegram ${method} failed (${response.status}): ${description}`,
      );
    }

    return envelope.result as T;
  }

  /**
   * Send a text message.
   *
   * Permanent 4xx failures (the user blocked the bot, the chat was deleted)
   * are logged and swallowed: retrying the webhook would only repeat the same
   * rejection. Transient failures still bubble up so Telegram retries.
   */
  async sendMessage(
    chatId: number,
    text: string,
    replyMarkup?: InlineKeyboardMarkup,
  ): Promise<void> {
    const clipped =
      text.length > MAX_MESSAGE_LENGTH
        ? `${text.slice(0, MAX_MESSAGE_LENGTH - 1)}…`
        : text;

    const payload: Record<string, unknown> = {
      chat_id: chatId,
      text: clipped,
      disable_web_page_preview: true,
    };
    if (replyMarkup) {
      payload.reply_markup = replyMarkup;
    }

    try {
      await this.call('sendMessage', payload);
    } catch (error) {
      if (
        error instanceof TelegramError &&
        error.status >= 400 &&
        error.status < 500
      ) {
        console.warn(
          JSON.stringify({
            level: 'warn',
            event: 'telegram_send_failed',
            status: error.status,
            detail: error.message,
          }),
        );
        return;
      }
      throw error;
    }
  }
}

/** Build the one-button keyboard that opens the mini-app. */
export function webAppKeyboard(url: string): InlineKeyboardMarkup {
  return {
    inline_keyboard: [[{ text: '🏆 مشاهده براکت زنده', web_app: { url } }]],
  };
}