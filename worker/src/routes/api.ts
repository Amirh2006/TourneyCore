import { Hono } from 'hono';

import type { Env } from '../env';
import { ApiError } from '../lib/errors';
import { buildDashboard, buildSystemList } from '../services/tournaments';

export const apiRoutes = new Hono<{ Bindings: Env }>();

const NO_STORE = { 'Cache-Control': 'no-store' } as const;

/** Liveness probe. */
apiRoutes.get('/health', (c) =>
  c.json(
    {
      status: 'ok',
      service: 'tourneycore-api',
      schema: 1,
      time: new Date().toISOString(),
    },
    200,
    NO_STORE,
  ),
);

apiRoutes.get('/', (c) =>
  c.json(
    {
      name: 'TourneyCore API',
      schema: 1,
      endpoints: ['/api/health', '/api/systems', '/api/systems/:id'],
    },
    200,
    NO_STORE,
  ),
);

/** Tournament selector. */
apiRoutes.get('/systems', async (c) => {
  const payload = await buildSystemList(c.env);
  return c.json(payload, 200, NO_STORE);
});

/** Full dashboard for one tournament. */
apiRoutes.get('/systems/:id', async (c) => {
  const systemId = c.req.param('id');
  const payload = await buildDashboard(c.env, systemId);
  if (payload === null) {
    throw new ApiError(
      404,
      'not_found',
      `Unknown tournament '${systemId}'.`,
    );
  }
  return c.json(payload, 200, NO_STORE);
});
