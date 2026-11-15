import type { Env } from '../env';
import { isMatchDead } from '../lib/bracket';
import { fetchOne, getSupabase } from '../lib/supabase';
import {
  API_SCHEMA_VERSION,
  type DashboardPayload,
  type MatchPayload,
  type MatchRow,
  type PlayerRef,
  type PlayerRow,
  type PlayerSummary,
  type SystemListPayload,
  type SystemRow,
} from '../types';
import {
  listMatches,
  listPlayers,
  listSystems,
  matchCountsBySystem,
  playerCountsBySystem,
} from './queries';

const ALIVE = new Set(['registered', 'active']);

function playerRef(player: PlayerRow | null | undefined): PlayerRef | null {
  if (!player) {
    return null;
  }
  return { id: player.id, name: player.name, seed: player.seed };
}

function playerSummary(player: PlayerRow): PlayerSummary {
  return {
    id: player.id,
    name: player.name,
    seed: player.seed,
    status: player.status,
    wins: player.wins,
    losses: player.losses,
    eliminated: player.status === 'eliminated',
    withdrawn: player.status === 'withdrawn',
  };
}

/** Fewest losses, then most wins, then name — same ordering as the bot. */
function orderPlayers(players: readonly PlayerRow[]): PlayerRow[] {
  return [...players].sort(
    (left, right) =>
      left.losses - right.losses ||
      right.wins - left.wins ||
      left.name.localeCompare(right.name),
  );
}

function serializeMatch(
  match: MatchRow,
  playersById: Map<string, PlayerRow>,
  matches: readonly MatchRow[],
): MatchPayload {
  return {
    id: match.id,
    bracket: match.bracket,
    round: match.round,
    position: match.position,
    status: match.status,
    player1: playerRef(
      match.player1_id ? playersById.get(match.player1_id) : null,
    ),
    player2: playerRef(
      match.player2_id ? playersById.get(match.player2_id) : null,
    ),
    winner: playerRef(match.winner_id ? playersById.get(match.winner_id) : null),
    loser: playerRef(match.loser_id ? playersById.get(match.loser_id) : null),
    score: {
      player1: match.score_player1,
      player2: match.score_player2,
    },
    is_bye: match.is_bye,
    is_dead: isMatchDead(matches, match),
    started_at: match.started_at,
    completed_at: match.completed_at,
  };
}

/**
 * Tournament selector payload, newest first.
 *
 * `ready_count` lets the mini-app highlight an event with matches waiting to
 * be played without fetching every dashboard.
 */
export async function buildSystemList(env: Env): Promise<SystemListPayload> {
  const [systems, playerCounts, matchCounts] = await Promise.all([
    listSystems(env),
    playerCountsBySystem(env),
    matchCountsBySystem(env),
  ]);

  const items = [...systems]
    .sort((left, right) =>
      (right.created_at ?? '').localeCompare(left.created_at ?? ''),
    )
    .map((system) => {
      const matches = matchCounts.get(system.id) ?? { total: 0, ready: 0 };
      return {
        id: system.id,
        name: system.name,
        description: system.description ?? '',
        status: system.status,
        registration_open: system.registration_open,
        max_players: system.max_players,
        player_count: playerCounts.get(system.id) ?? 0,
        match_count: matches.total,
        ready_count: matches.ready,
        created_at: system.created_at,
        updated_at: system.updated_at,
      };
    });

  return { schema: API_SCHEMA_VERSION, systems: items };
}

/**
 * Everything the mini-app needs to render one tournament.
 *
 * Returns `null` when the id is unknown so the route can answer 404 without
 * leaking whether the failure came from PostgREST.
 */
export async function buildDashboard(
  env: Env,
  systemId: string,
): Promise<DashboardPayload | null> {
  const system = await fetchOne<SystemRow>(
    getSupabase(env),
    'systems',
    'id',
    systemId,
  );
  if (!system) {
    return null;
  }

  const [players, matches] = await Promise.all([
    listPlayers(env, system.id),
    listMatches(env, system.id),
  ]);

  const playersById = new Map(players.map((player) => [player.id, player]));
  const orderedMatches = [...matches].sort(
    (left, right) => left.round - right.round || left.position - right.position,
  );

  const brackets: DashboardPayload['brackets'] = {
    winners: [],
    losers: [],
    grand_final: [],
  };

  for (const match of orderedMatches) {
    const payload = serializeMatch(match, playersById, matches);
    if (match.bracket === 'winners') {
      brackets.winners.push(payload);
    } else if (match.bracket === 'losers') {
      brackets.losers.push(payload);
    } else if (match.bracket === 'grand_final') {
      brackets.grand_final.push(payload);
    }
  }

  const alive = players.filter((player) => ALIVE.has(player.status));
  const champion =
    system.status === 'completed' && alive.length === 1 ? alive[0] : null;

  return {
    schema: API_SCHEMA_VERSION,
    generated_at: new Date().toISOString(),
    system: {
      id: system.id,
      name: system.name,
      description: system.description ?? '',
      status: system.status,
      registration_open: system.registration_open,
      max_players: system.max_players,
      player_count: players.length,
      created_at: system.created_at,
      updated_at: system.updated_at,
    },
    champion: playerRef(champion),
    players: orderPlayers(players).map(playerSummary),
    brackets,
    counts: {
      players: players.length,
      matches: matches.length,
      completed: matches.filter((match) => match.status === 'completed').length,
      ready: matches.filter((match) => match.status === 'ready').length,
      active: matches.filter((match) => match.status === 'active').length,
      byes: matches.filter((match) => match.is_bye).length,
      remaining: alive.length,
    },
  };
}
