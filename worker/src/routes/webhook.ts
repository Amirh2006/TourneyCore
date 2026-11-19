import { Hono, type Context } from 'hono';

import type { Env } from '../env';
import { ApiError, BotError } from '../lib/errors';
import { getSupabase } from '../lib/supabase';
import {
  TelegramClient,
  webAppKeyboard,
  type TelegramMessage,
  type TelegramUpdate,
  type TelegramUser,
} from '../lib/telegram';
import {
  WELCOME_TEXT,
  findNextMatch,
  formatBracket,
  formatNextMatch,
  formatProfile,
  formatStandings,
} from '../services/bot-format';
import { findPlayerByTelegramId, redeemEntryCode } from '../services/players';
import { listMatches, listPlayers } from '../services/queries';
import type { PlayerRow } from '../types';

export const webhookRoutes = new Hono<{ Bindings: Env }>();

webhookRoutes.onError((err, c) => {
  console.error('FATAL SERVER ERROR:', err.name, err.message, err.stack);
  // Returning ok: true causes Telegram to discard queued processed messages
  return c.json({ ok: true });
});

/**
 * `POST /webhook` — the only Telegram entry point.
 */
webhookRoutes.post('/webhook', async (c) => {
  assertWebhookSecret(c);

  let update: TelegramUpdate;
  try {
    update = await c.req.json<TelegramUpdate>();
  } catch {
    throw new ApiError(400, 'bad_request', 'Request body must be valid JSON.');
  }

  if (
    update === null ||
    typeof update !== 'object' ||
    typeof update.update_id !== 'number'
  ) {
    throw new ApiError(
      400,
      'bad_request',
      'Request body is not a Telegram update.',
    );
  }

  await handleUpdate(c.env, update);
  return c.json({ ok: true });
});

function assertWebhookSecret(c: Context<{ Bindings: Env }>): void {
  const expected = c.env.TELEGRAM_WEBHOOK_SECRET;
  if (!expected) {
    return;
  }

  const provided = c.req.header('X-Telegram-Bot-Api-Secret-Token') ?? '';
  if (!safeEqual(expected, provided)) {
    throw new ApiError(
      401,
      'unauthorized',
      'Invalid Telegram webhook secret token.',
    );
  }
}

/** Constant-time string comparison for the webhook secret. */
function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) {
    return false;
  }
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

/** Link any unlinked player rows matching this Telegram user's username. */
async function linkPlayerByUsername(
  env: Env,
  telegramId: number,
  username: string | undefined,
): Promise<void> {
  if (!username) {
    return;
  }
  const normalized = username.replace(/^@+/, '').trim().toLowerCase();
  if (!normalized) {
    return;
  }
  try {
    const supabase = getSupabase(env);
    await supabase
      .from('players')
      .update({ telegram_id: telegramId, telegram_username: normalized })
      .ilike('telegram_username', normalized)
      .is('telegram_id', null);
  } catch (error) {
    console.error('Failed to link player by username in webhook:', error);
  }
}

async function handleUpdate(env: Env, update: TelegramUpdate): Promise<void> {
  const message = update.message;
  if (!message?.from || message.from.is_bot) {
    return;
  }

  const text = (message.text ?? '').trim();
  if (!text) {
    return;
  }

  // Auto-link player account by username before routing commands
  await linkPlayerByUsername(env, message.from.id, message.from.username);

  const bot = new TelegramClient(env.TELEGRAM_BOT_TOKEN);

  try {
    await routeMessage(env, bot, message, message.from, text);
  } catch (error) {
    if (error instanceof BotError) {
      // A user error is not a webhook failure: answer them and acknowledge.
      await bot.sendMessage(message.chat.id, error.message);
      return;
    }
    throw error;
  }
}

async function routeMessage(
  env: Env,
  bot: TelegramClient,
  message: TelegramMessage,
  from: TelegramUser,
  text: string,
): Promise<void> {
  const chatId = message.chat.id;
  const firstToken = text.split(/\s+/, 1)[0] ?? '';
  const isCommand = firstToken.startsWith('/');
  const command = isCommand
    ? firstToken.slice(1).split('@')[0]?.toLowerCase() ?? ''
    : '';
  const args = isCommand ? text.slice(firstToken.length).trim() : text;

  switch (command) {
    case 'start': {
      const player = await findPlayerByTelegramId(env, from.id);
      const greeting = player
        ? `Welcome back, ${player.name}!\n\n${WELCOME_TEXT}`
        : WELCOME_TEXT;
      const appUrl = miniAppUrl(env);
      await bot.sendMessage(
        chatId,
        greeting,
        appUrl ? webAppKeyboard(appUrl) : undefined,
      );
      return;
    }

    case 'help':
      await bot.sendMessage(chatId, WELCOME_TEXT);
      return;

    case 'app': {
      const appUrl = miniAppUrl(env);
      if (!appUrl) {
        await bot.sendMessage(
          chatId,
          'The live bracket mini-app is not configured yet. Ask the ' +
            'organizer to set MINI_APP_URL to a public HTTPS URL.',
        );
        return;
      }
      await bot.sendMessage(
        chatId,
        '📺 The bracket updates live while you watch.',
        webAppKeyboard(appUrl),
      );
      return;
    }

    case 'register':
      await doRegister(env, bot, chatId, from.id, from.username, args);
      return;

    case 'me': {
      const player = await requirePlayer(env, bot, chatId, from.id);
      if (player) {
        await bot.sendMessage(chatId, formatProfile(player));
      }
      return;
    }

    case 'next': {
      const player = await requirePlayer(env, bot, chatId, from.id);
      if (!player) {
        return;
      }
      const [players, matches] = await Promise.all([
        listPlayers(env, player.system_id),
        listMatches(env, player.system_id),
      ]);
      const upcoming = findNextMatch(matches, player.id);
      await bot.sendMessage(
        chatId,
        formatNextMatch(upcoming, players, player.id),
      );
      return;
    }

    case 'bracket': {
      const player = await requirePlayer(env, bot, chatId, from.id);
      if (!player) {
        return;
      }
      const [players, matches] = await Promise.all([
        listPlayers(env, player.system_id),
        listMatches(env, player.system_id),
      ]);
      await bot.sendMessage(chatId, formatBracket(players, matches));
      return;
    }

    case 'standings': {
      const player = await requirePlayer(env, bot, chatId, from.id);
      if (!player) {
        return;
      }
      const players = await listPlayers(env, player.system_id);
      await bot.sendMessage(chatId, formatStandings(players));
      return;
    }

    default:
      // Scenario A: a bare 8-character code from a player's ticket.
      if (!isCommand && /^[A-Za-z0-9]{8}$/.test(text)) {
        await doRegister(env, bot, chatId, from.id, from.username, text);
      }
  }
}

async function doRegister(
  env: Env,
  bot: TelegramClient,
  chatId: number,
  telegramId: number,
  username: string | undefined,
  code: string,
): Promise<void> {
  if (!code) {
    await bot.sendMessage(chatId, 'Usage: /register A1B2C3D4');
    return;
  }

  try {
    const player = await redeemEntryCode(
      env,
      code,
      telegramId,
      username ?? null,
    );

    await bot.sendMessage(
      chatId,
      `✅ Registered as ${player.name}!\n` +
        `Your entry code ${player.entry_code} is now linked to this account.`,
    );
  } catch (error) {
    if (error instanceof BotError) {
      throw new BotError(`Registration failed: ${error.message}`);
    }
    throw error;
  }
}

/** Return the mini-app URL only when Telegram can actually open it. */
function miniAppUrl(env: Env): string | null {
  const url = env.MINI_APP_URL?.trim();
  if (!url) {
    return null;
  }
  if (!url.startsWith('https://')) {
    console.warn(
      JSON.stringify({
        level: 'warn',
        event: 'invalid_mini_app_url',
        detail: 'MINI_APP_URL must start with https://; ignoring it.',
      }),
    );
    return null;
  }
  return url;
}

async function requirePlayer(
  env: Env,
  bot: TelegramClient,
  chatId: number,
  telegramId: number,
): Promise<PlayerRow | null> {
  const player = await findPlayerByTelegramId(env, telegramId);
  if (!player) {
    await bot.sendMessage(
      chatId,
      'You are not registered yet. Send the entry code from your admin, ' +
        'for example: /register A1B2C3D4',
    );
    return null;
  }
  return player;
}