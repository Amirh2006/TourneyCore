import { createClient, type SupabaseClient } from '@supabase/supabase-js';

import type { Env } from '../env';
import { ApiError } from './errors';

/** PostgREST returns at most this many rows per request by default. */
const PAGE_SIZE = 1_000;

let cached: { url: string; key: string; client: SupabaseClient } | null = null;

/**
 * Return a Supabase client for the current isolate.
 *
 * The client is cached per (URL, key) pair because Workers reuse isolates
 * between requests. It uses the service-role key and disables every
 * browser-oriented auth feature, so no session state is stored or refreshed.
 */
export function getSupabase(env: Env): SupabaseClient {
  const url = env.SUPABASE_URL?.trim().replace(/\/+$/, '');
  const key = env.SUPABASE_SERVICE_ROLE_KEY?.trim();

  if (!url || !key) {
    throw new ApiError(
      500,
      'configuration_error',
      'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.',
    );
  }

  if (cached && cached.url === url && cached.key === key) {
    return cached.client;
  }

  const client = createClient(url, key, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    global: {
      headers: { 'X-Client-Info': 'tourneycore-worker/1.0' },
    },
  });

  cached = { url, key, client };
  return client;
}

export interface FetchAllOptions {
  /** Columns to select. Defaults to `*`. */
  columns?: string;
  /** Optional equality filter, used to scope reads to one tournament. */
  filter?: { column: string; value: string };
}

/**
 * Read every row of a table, following PostgREST pagination.
 *
 * Supabase caps a single response (1000 rows by default). Paging by `id`
 * keeps the results deterministic and prevents a large tournament history
 * from silently truncating the dashboard counts.
 */
export async function fetchAll<T>(
  client: SupabaseClient,
  table: string,
  options: FetchAllOptions = {},
): Promise<T[]> {
  const columns = options.columns ?? '*';
  const rows: T[] = [];

  for (let from = 0; ; from += PAGE_SIZE) {
    let query = client
      .from(table)
      .select(columns)
      .order('id', { ascending: true })
      .range(from, from + PAGE_SIZE - 1);

    if (options.filter) {
      query = query.eq(options.filter.column, options.filter.value);
    }

    const { data, error } = await query;
    if (error) {
      throw new ApiError(
        502,
        'database_error',
        `Could not read ${table}: ${error.message}`,
      );
    }

    const batch = (data ?? []) as unknown as T[];
    rows.push(...batch);
    if (batch.length < PAGE_SIZE) {
      return rows;
    }
  }
}

/** Read a single row by a unique column, or `null` when it does not exist. */
export async function fetchOne<T>(
  client: SupabaseClient,
  table: string,
  column: string,
  value: string,
): Promise<T | null> {
  const { data, error } = await client
    .from(table)
    .select('*')
    .eq(column, value)
    .maybeSingle();

  if (error) {
    throw new ApiError(
      502,
      'database_error',
      `Could not read ${table}: ${error.message}`,
    );
  }

  return (data as T | null) ?? null;
}
