import type { Context, Next } from 'hono';
import { getSupabase } from './supabase';
import type { Env } from '../env';

const ADMIN_IDS = [8725080670, 999999];
const MAX_INIT_DATA_AGE_SECONDS = 86400; // 24 hours

// Precise type definitions for Hono context variables
type AppContext = {
  Bindings: Env;
  Variables: {
    user: { id: number | null; username: string | null };
    role: string;
  };
};

interface VerifiedTelegramUser {
  id: number;
  username: string | null;
}

/** Constant-time string comparison to prevent timing attacks. */
function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) {
    return false;
  }
  let difference = 0;
  for (let i = 0; i < left.length; i += 1) {
    difference |= left.charCodeAt(i) ^ right.charCodeAt(i);
  }
  return difference === 0;
}

/** Convert an ArrayBuffer digest into a lowercase hexadecimal string. */
function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Cryptographically verify Telegram Mini App initData using HMAC-SHA256.
 * Returns the verified user payload or null if validation fails.
 */
async function verifyTelegramInitData(
  initData: string,
  botToken: string | undefined,
): Promise<VerifiedTelegramUser | null> {
  if (!botToken) {
    console.error('TELEGRAM_BOT_TOKEN is missing; rejecting initData.');
    return null;
  }

  try {
    const params = new URLSearchParams(initData);
    const receivedHash = params.get('hash');
    if (!receivedHash) {
      return null;
    }

    // Reject expired initData payloads to mitigate replay attacks
    const authDateRaw = params.get('auth_date');
    const authDate = authDateRaw ? Number(authDateRaw) : NaN;
    const nowSeconds = Math.floor(Date.now() / 1000);
    if (!Number.isFinite(authDate) || nowSeconds - authDate > MAX_INIT_DATA_AGE_SECONDS) {
      return null;
    }

    // Build data-check-string by sorting all key=value pairs alphabetically excluding hash
    const pairs: Array<[string, string]> = [];
    params.forEach((value, key) => {
      if (key !== 'hash') {
        pairs.push([key, value]);
      }
    });
    pairs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    const dataCheckString = pairs.map(([k, v]) => `${k}=${v}`).join('\n');

    const encoder = new TextEncoder();

    // Derive secret key: HMAC_SHA256(key="WebAppData", message=botToken)
    const webAppKey = await crypto.subtle.importKey(
      'raw',
      encoder.encode('WebAppData'),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const secretKeyBytes = await crypto.subtle.sign(
      'HMAC',
      webAppKey,
      encoder.encode(botToken),
    );

    // Compute expected hash: HMAC_SHA256(key=secretKeyBytes, message=dataCheckString)
    const signingKey = await crypto.subtle.importKey(
      'raw',
      secretKeyBytes,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const signature = await crypto.subtle.sign(
      'HMAC',
      signingKey,
      encoder.encode(dataCheckString),
    );
    const expectedHash = toHex(signature);

    if (!safeEqual(expectedHash, receivedHash.toLowerCase())) {
      return null;
    }

    const userStr = params.get('user');
    if (!userStr) {
      return null;
    }

    const userObj = JSON.parse(userStr) as { id?: unknown; username?: unknown };
    const id = Number(userObj.id);
    if (!Number.isFinite(id) || id <= 0) {
      return null;
    }

    const username =
      typeof userObj.username === 'string' && userObj.username.trim()
        ? userObj.username.replace(/^@+/, '').trim().toLowerCase()
        : null;

    return { id, username };
  } catch (error) {
    console.error('Failed to verify Telegram initData:', error);
    return null;
  }
}

export async function authMiddleware(c: Context<AppContext>, next: Next) {
  const initData = c.req.header('X-Telegram-Init-Data');
  let telegramId: number | null = null;
  let telegramUsername: string | null = null;

  if (initData) {
    const verifiedUser = await verifyTelegramInitData(initData, c.env.TELEGRAM_BOT_TOKEN);
    if (verifiedUser) {
      telegramId = verifiedUser.id;
      telegramUsername = verifiedUser.username;
    }
  }

  let role = 'guest';

  if (telegramId) {
    const supabase = getSupabase(c.env);

    // Automatically link unlinked player rows matching this Telegram username
    if (telegramUsername) {
      try {
        await supabase
          .from('players')
          .update({ telegram_id: telegramId, telegram_username: telegramUsername })
          .ilike('telegram_username', telegramUsername)
          .is('telegram_id', null);
      } catch (e) {
        console.error('Failed to auto-link player by username:', e);
      }
    }

    if (ADMIN_IDS.includes(telegramId)) {
      role = 'admin';
    } else {
      const { data } = await supabase
        .from('user_roles')
        .select('role')
        .eq('telegram_id', telegramId)
        .single();

      if (data && data.role) {
        role = data.role;
      } else {
        role = 'player';
      }
    }
  }

  c.set('user', { id: telegramId, username: telegramUsername });
  c.set('role', role);

  await next();
}

export async function requireAdmin(c: Context<AppContext>, next: Next) {
  if (c.get('role') !== 'admin') {
    return c.json({ error: 'forbidden', detail: 'Admin access required.' }, 403);
  }
  await next();
}

export async function requireCoworker(c: Context<AppContext>, next: Next) {
  const role = c.get('role');
  if (role !== 'admin' && role !== 'coworker') {
    return c.json({ error: 'forbidden', detail: 'Coworker access required.' }, 403);
  }
  await next();
}