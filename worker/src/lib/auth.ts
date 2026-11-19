import type { Context, Next } from 'hono';
import { getSupabase } from './supabase';
import type { Env } from '../env';

const ADMIN_IDS = [8725080670, 999999];

// Precise type definitions for Hono context variables
type AppContext = {
  Bindings: Env;
  Variables: {
    user: { id: number | null; username: string | null };
    role: string;
  };
};

export async function authMiddleware(c: Context<AppContext>, next: Next) {
  const initData = c.req.header('X-Telegram-Init-Data');
  let telegramId: number | null = null;
  let telegramUsername: string | null = null;

  if (initData) {
    try {
      const urlParams = new URLSearchParams(initData);
      const userStr = urlParams.get('user');
      if (userStr) {
        const userObj = JSON.parse(userStr);
        telegramId = userObj.id ? Number(userObj.id) : null;
        telegramUsername =
          typeof userObj.username === 'string' && userObj.username.trim()
            ? userObj.username.replace(/^@+/, '').trim().toLowerCase()
            : null;
      }
    } catch (e) {
      console.error('Failed to parse initData', e);
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