import { Hono } from 'hono';
import type { Env } from '../env';
import { ApiError } from '../lib/errors';
import {
  buildDashboard,
  buildSystemList,
  createSystem,
  deleteSystem,
  addPlayer,
  startBracket,
  promoteCoworker,
  updateMatch
} from '../services/tournaments';
import { requireAdmin, requireCoworker } from '../lib/auth';

export const apiRoutes = new Hono<{
  Bindings: Env;
  Variables: { user: any; role: string }
}>();

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
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing tournament ID.');
  await deleteSystem(c.env, systemId);
  return c.json({ status: 'success' }, 200);
});

apiRoutes.post('/systems/:id/players', requireCoworker, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing tournament ID.');
  const body = await c.req.json();
  await addPlayer(c.env, systemId, body.name);
  return c.json({ status: 'success', message: 'Player added.' }, 201);
});

apiRoutes.post('/systems/:id/start', requireCoworker, async (c) => {
  const systemId = c.req.param('id');
  if (!systemId) throw new ApiError(400, 'bad_request', 'Missing tournament ID.');
  await startBracket(c.env, systemId);
  return c.json({ status: 'success', message: 'Bracket started.' }, 200);
});

apiRoutes.put('/matches/:id', requireCoworker, async (c) => {
  const matchId = c.req.param('id');
  if (!matchId) throw new ApiError(400, 'bad_request', 'Missing match ID.');
  const body = await c.req.json();
  await updateMatch(c.env, matchId, body);
  return c.json({ status: 'success', message: `Match ${matchId} updated.` }, 200);
});

apiRoutes.post('/roles/promote', requireAdmin, async (c) => {
  const body = await c.req.json();
  if (!body.telegram_id || !body.new_role) throw new ApiError(400, 'bad_request', 'Missing IDs.');
  await promoteCoworker(c.env, body.telegram_id);
  return c.json({ status: 'success' }, 200);
});