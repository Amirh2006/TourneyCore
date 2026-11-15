import type { Env } from '../env';
import { fetchAll, getSupabase } from '../lib/supabase';
import type { MatchRow, PlayerRow, SystemRow } from '../types';

/** All tournaments, in database order. */
export function listSystems(env: Env): Promise<SystemRow[]> {
  return fetchAll<SystemRow>(getSupabase(env), 'systems');
}

/** Players of one tournament, or every player when `systemId` is omitted. */
export function listPlayers(env: Env, systemId?: string): Promise<PlayerRow[]> {
  return fetchAll<PlayerRow>(getSupabase(env), 'players', {
    filter: systemId ? { column: 'system_id', value: systemId } : undefined,
  });
}

/** Matches of one tournament, or every match when `systemId` is omitted. */
export function listMatches(env: Env, systemId?: string): Promise<MatchRow[]> {
  return fetchAll<MatchRow>(getSupabase(env), 'matches', {
    filter: systemId ? { column: 'system_id', value: systemId } : undefined,
  });
}

/**
 * Count players per tournament without transferring full player records.
 *
 * The systems list only needs aggregate numbers; reading two narrow columns
 * is considerably cheaper than hydrating every row on a busy event.
 */
export async function playerCountsBySystem(
  env: Env,
): Promise<Map<string, number>> {
  const rows = await fetchAll<{ id: string; system_id: string }>(
    getSupabase(env),
    'players',
    { columns: 'id,system_id' },
  );

  const counts = new Map<string, number>();
  for (const row of rows) {
    counts.set(row.system_id, (counts.get(row.system_id) ?? 0) + 1);
  }
  return counts;
}

/** Count all and ready matches per tournament. */
export async function matchCountsBySystem(
  env: Env,
): Promise<Map<string, { total: number; ready: number }>> {
  const rows = await fetchAll<{ id: string; system_id: string; status: string }>(
    getSupabase(env),
    'matches',
    { columns: 'id,system_id,status' },
  );

  const counts = new Map<string, { total: number; ready: number }>();
  for (const row of rows) {
    const current = counts.get(row.system_id) ?? { total: 0, ready: 0 };
    current.total += 1;
    if (row.status === 'ready') {
      current.ready += 1;
    }
    counts.set(row.system_id, current);
  }
  return counts;
}
