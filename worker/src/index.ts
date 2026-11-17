import { Hono } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { authMiddleware } from './lib/auth';
import type { Env } from './env';
import { ApiError } from './lib/errors';
import { apiRoutes } from './routes/api';
import { webhookRoutes } from './routes/webhook';

const app = new Hono<{ Bindings: Env }>();

app.use('*', async (c, next) => {
  const startedAt = Date.now();
  await next();
  console.log(
    JSON.stringify({
      level: 'info',
      method: c.req.method,
      path: c.req.path,
      status: c.res.status,
      duration_ms: Date.now() - startedAt,
    }),
  );
});

app.use('*', async (c, next) => {
  c.header('Access-Control-Allow-Origin', '*');
  c.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  c.header(
    'Access-Control-Allow-Headers',
    'Content-Type, Authorization, X-Requested-With, X-Telegram-Init-Data, Accept',
  );
  c.header('Access-Control-Max-Age', '86400');
  c.header('Vary', 'Origin');
  c.header('X-Content-Type-Options', 'nosniff');
  c.header('Referrer-Policy', 'no-referrer');

  if (c.req.method === 'OPTIONS') {
    return c.body(null, 204);
  }

  await next();
});

app.use('/api/*', authMiddleware);
app.route('/', webhookRoutes);
app.route('/api', apiRoutes);

app.get('/', (c) =>
  c.json({
    name: 'TourneyCore Worker',
    schema: 1,
    webhook: 'POST /webhook',
    api: ['GET /api/health', 'GET /api/systems', 'GET /api/systems/:id'],
  }),
);

app.notFound((c) =>
  c.json({ error: 'not_found', detail: `Unknown endpoint '${c.req.path}'.` }, 404),
);

app.onError((error, c) => {
  if (error instanceof ApiError) {
    return c.json({ error: error.code, detail: error.message }, error.status);
  }
  if (error instanceof HTTPException) {
    return error.getResponse();
  }
  console.error(
    JSON.stringify({
      level: 'error',
      path: c.req.path,
      method: c.req.method,
      message: error.message,
      stack: error.stack,
    }),
  );
  return c.json({ error: 'internal_error', detail: 'Unexpected server error.' }, 500);
});

export default app;