import { Hono } from 'hono';
import type { Env } from '../env';
import { ApiError } from '../lib/errors';
import {
  buildDashboard, buildSystemList, createSystem, deleteSystem,
  addPlayer, startBracket, promoteCoworker, updateMatch
} from '../services/tournaments';
import { requireAdmin, requireCoworker } from '../lib/auth';

export const apiRoutes = new Hono<{ Bindings: Env; Variables: { user: any; role: string } }>();
const NO_STORE = { 'Cache-Control': 'no-store' } as const;

apiRoutes.get('/health', (c) => c.json({ status: 'ok', schema: 1 }, 200, NO_STORE));
apiRoutes.get('/', (c) => c.json({ schema: 1, endpoints: ['/api/systems'] }, 200, NO_STORE));

apiRoutes.get('/systems', async (c) => {
  const payload = await buildSystemList(c.env);
  return c.json({ ...payload, userRole: c.get('role') }, 200, NO_STORE);
});

apiRoutes.get('/systems/:id', async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing tournament ID.');
  const payload = await buildDashboard(c.env, systemId);
  if (payload === null) throw new ApiError(404, 'not_found', `Unknown tournament '${systemId}'.`);
  return c.json({ ...payload, userRole: c.get('role') }, 200, NO_STORE);
});

apiRoutes.post('/systems', requireCoworker, async (c) => {
  const body = await c.req.json();
  const newSystem = await createSystem(c.env, body.name);
  return c.json({ status: 'success', system: newSystem }, 201);
});

apiRoutes.delete('/systems/:id', requireAdmin, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing ID.');
  await deleteSystem(c.env, systemId);
  return c.json({ status: 'success' }, 200);
});

apiRoutes.post('/systems/:id/players', requireCoworker, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing ID.');
  const body = await c.req.json();
  await addPlayer(c.env, systemId, body.name, body.telegram_id);
  return c.json({ status: 'success' }, 201);
});

apiRoutes.post('/systems/:id/start', requireCoworker, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing ID.');
  await startBracket(c.env, systemId);
  return c.json({ status: 'success' }, 200);
});

apiRoutes.put('/matches/:id', requireCoworker, async (c) => {
  const matchId = c.req.param('id');
  if (!matchId) throw new ApiError(400, 'bad_request', 'Missing match ID.');
  const body = await c.req.json();
  await updateMatch(c.env, matchId, body);
  return c.json({ status: 'success' }, 200);
});

apiRoutes.post('/roles/promote', requireAdmin, async (c) => {
  const body = await c.req.json();
  await promoteCoworker(c.env, body.telegram_id);
  return c.json({ status: 'success' }, 200);
});

// Single match notification via Telegram Bot API
apiRoutes.post('/matches/:id/notify', requireCoworker, async (c) => {
  const matchId = c.req.param('id');
  if (!matchId) throw new ApiError(400, 'bad_request', 'Missing match ID.');
  const body = await c.req.json();
  const supabaseUrl = c.env.SUPABASE_URL;
  const supabaseKey = c.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!supabaseUrl || !supabaseKey) throw new ApiError(500, 'server_error', 'Supabase credentials missing.');

  const matchRes = await fetch(`${supabaseUrl}/rest/v1/matches?id=eq.${matchId}&select=player1_id,player2_id`, {
    headers: { apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}`, Accept: 'application/json' }
  });
  const matches = (await matchRes.json()) as any[];
  if (!matches || matches.length === 0) throw new ApiError(404, 'not_found', 'Match not found.');

  const pIds = [matches[0].player1_id, matches[0].player2_id].filter(Boolean);
  let targetIds: (number | string)[] = [];

  if (pIds.length > 0) {
    const playersRes = await fetch(`${supabaseUrl}/rest/v1/players?id=in.(${pIds.join(',')})&select=telegram_id`, {
      headers: { apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}`, Accept: 'application/json' }
    });
    const players = (await playersRes.json()) as any[];
    targetIds = players.map(p => p.telegram_id).filter(Boolean);
  }

  if (targetIds.length === 0) throw new ApiError(400, 'bad_request', 'Neither player has a Telegram ID.');

  const botToken = c.env.TELEGRAM_BOT_TOKEN;
  for (const chatId of targetIds) {
    try {
      await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text: `🔔 Match Alert:\n\n${body.message}` })
      });
    } catch (e) { console.error(`Error: ${e}`); }
  }
  return c.json({ status: 'success' }, 200);
});

// Broadcast route (In-App Banner with History)
apiRoutes.post('/systems/:id/broadcast', requireCoworker, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing ID.');
  const body = await c.req.json();
  const message = body.message || null;

  const supabaseUrl = c.env.SUPABASE_URL;
  const supabaseKey = c.env.SUPABASE_SERVICE_ROLE_KEY;

  // 1. Fetch previous announcements from the database
  const sysRes = await fetch(`${supabaseUrl}/rest/v1/systems?id=eq.${systemId}&select=announcement`, {
    headers: { apikey: supabaseKey, Authorization: `Bearer ${supabaseKey}`, Accept: 'application/json' }
  });
  const sysData = (await sysRes.json()) as any[];
  if (!sysData || sysData.length === 0) throw new ApiError(404, 'not_found', 'System not found.');

  let announcements: Array<{text: string, date: string}> = [];
  try {
    if (sysData[0].announcement) {
      const parsed = JSON.parse(sysData[0].announcement);
      if (Array.isArray(parsed)) announcements = parsed;
      else announcements = [{ text: sysData[0].announcement, date: new Date().toISOString() }];
    }
  } catch (e) {
    // Fallback if previous data was a simple string (not JSON)
    if (sysData[0].announcement) {
      announcements = [{ text: sysData[0].announcement, date: new Date().toISOString() }];
    }
  }

  // 2. Add new message to the top of the list (or clear if empty)
  if (message) {
    announcements.unshift({ text: message, date: new Date().toISOString() });
  } else {
    announcements = [];
  }

  const payload = announcements.length > 0 ? JSON.stringify(announcements) : null;

  // 3. Save the updated list to the database
  const response = await fetch(`${supabaseUrl}/rest/v1/systems?id=eq.${systemId}`, {
    method: 'PATCH',
    headers: {
      apikey: supabaseKey,
      Authorization: `Bearer ${supabaseKey}`,
      'Content-Type': 'application/json',
      Prefer: 'return=minimal'
    },
    body: JSON.stringify({ announcement: payload })
  });

  if (!response.ok) {
    const dbError = await response.text();
    throw new ApiError(500, 'database_error', `Failed to save announcement: ${dbError}`);
  }
  return c.json({ status: 'success' }, 200);
});