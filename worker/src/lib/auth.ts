import type { Context, Next } from 'hono';
import { getSupabase } from './supabase';
import type { Env } from '../env';

const ADMIN_IDS = [8725080670, 999999];

// 🚀 تعریف دقیق تایپ‌ها برای رفع خطاهای Typescript در Hono
type AppContext = {
  Bindings: Env;
  Variables: {
    user: { id: number | null };
    role: string;
  };
};

export async function authMiddleware(c: Context<AppContext>, next: Next) {
  const initData = c.req.header('X-Telegram-Init-Data');
  let telegramId: number | null = null; // تعیین دقیق نوع متغیر

  if (initData) {
    try {
      const urlParams = new URLSearchParams(initData);
      const userStr = urlParams.get('user');
      if (userStr) {
        const userObj = JSON.parse(userStr);
        telegramId = Number(userObj.id);
      }
    } catch (e) {
      console.error('Failed to parse initData', e);
    }
  }

  let role = 'guest';

  if (telegramId) {
    if (ADMIN_IDS.includes(telegramId)) {
      role = 'admin';
    } else {
      const supabase = getSupabase(c.env);
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

  c.set('user', { id: telegramId });
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