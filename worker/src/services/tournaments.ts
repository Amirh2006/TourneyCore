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
import { ApiError } from '../lib/errors';

const ALIVE = new Set(['registered', 'active']);

function playerRef(player: PlayerRow | null | undefined): PlayerRef | null {
  if (!player) return null;
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

function orderPlayers(players: readonly PlayerRow[]): PlayerRow[] {
  return [...players].sort(
    (left, right) =>
      left.losses - right.losses ||
      right.wins - left.wins ||
      left.name.localeCompare(right.name),
  );
}

function serializeMatch(match: MatchRow, playersById: Map<string, PlayerRow>, matches: readonly MatchRow[]): MatchPayload {
  return {
    id: match.id,
    bracket: match.bracket,
    round: match.round,
    position: match.position,
    status: match.status,
    player1: playerRef(match.player1_id ? playersById.get(match.player1_id) : null),
    player2: playerRef(match.player2_id ? playersById.get(match.player2_id) : null),
    winner: playerRef(match.winner_id ? playersById.get(match.winner_id) : null),
    loser: playerRef(match.loser_id ? playersById.get(match.loser_id) : null),
    score: { player1: match.score_player1, player2: match.score_player2 },
    is_bye: match.is_bye,
    is_dead: isMatchDead(matches, match),
    started_at: match.started_at,
    completed_at: match.completed_at,
  };
}

export async function createSystem(env: Env, name: string): Promise<SystemRow> {
  const supabase = getSupabase(env);
  if (!name || typeof name !== 'string' || name.trim().length === 0) {
      throw new ApiError(400, 'invalid_input', 'Tournament name is required.');
  }
  const { data, error } = await supabase.from('systems').insert([{ name: name.trim(), status: 'pending', registration_open: true }]).select().single();
  if (error) throw new ApiError(500, 'database_error', `Failed to create tournament: ${error.message}`);
  return data as SystemRow;
}

export async function deleteSystem(env: Env, systemId: string): Promise<void> {
  const supabase = getSupabase(env);
  await supabase.from('matches').delete().eq('system_id', systemId);
  await supabase.from('players').delete().eq('system_id', systemId);
  const { error } = await supabase.from('systems').delete().eq('id', systemId);
  if (error) throw new ApiError(500, 'database_error', `Failed to delete tournament: ${error.message}`);
}

// Normalize and store telegram_username when adding a player manually
export async function addPlayer(
  env: Env,
  systemId: string,
  name: string,
  telegramUsername?: string | null,
): Promise<void> {
  const supabase = getSupabase(env);
  if (!name || name.trim() === '') {
    throw new ApiError(400, 'invalid_input', 'Player name is required');
  }

  const normalizedUsername = telegramUsername
    ? telegramUsername.replace(/^@+/, '').trim().toLowerCase() || null
    : null;

  const row = {
    name: name.trim(),
    system_id: systemId,
    status: 'registered',
    role: 'player',
    wins: 0,
    losses: 0,
    telegram_username: normalizedUsername,
  };

  const { error } = await supabase.from('players').insert([row]);
  if (error) {
    if (error.code === '23502' && error.message.includes('id')) {
      const randomId = Math.floor(Math.random() * 1000000);
      await supabase.from('players').insert([{ id: randomId, ...row }]);
    } else {
      throw new ApiError(500, 'database_error', `Failed to add player: ${error.message}`);
    }
  }
}

export async function startBracket(env: Env, systemId: string): Promise<void> {
  const supabase = getSupabase(env);
  const { data: players, error: pError } = await supabase.from('players').select('*').eq('system_id', systemId).eq('status', 'registered');
  if (pError) throw new ApiError(500, 'database_error', `Failed to fetch players: ${pError.message}`);
  if (!players || players.length < 2) throw new ApiError(400, 'bad_request', 'At least 2 players are required.');

  const { error: sError } = await supabase.from('systems').update({ status: 'active', registration_open: false }).eq('id', systemId);
  if (sError) throw new ApiError(500, 'database_error', `Failed to update system: ${sError.message}`);

  const shuffled = players.sort(() => 0.5 - Math.random());
  const matchesToInsert = [];
  let position = 1;

  for (let i = 0; i < shuffled.length; i += 2) {
    const p1 = shuffled[i];
    const p2 = shuffled[i + 1];
    matchesToInsert.push({
      system_id: systemId, bracket: 'winners', round: 1, position: position++,
      status: p2 ? 'ready' : 'completed',
      player1_id: p1.id, player2_id: p2 ? p2.id : null,
      is_bye: !p2, winner_id: !p2 ? p1.id : null
    });
  }
  const { error: mError } = await supabase.from('matches').insert(matchesToInsert);
  if (mError) throw new ApiError(500, 'database_error', `Failed to generate matches: ${mError.message}`);
}

export async function promoteCoworker(env: Env, telegramId: number): Promise<void> {
  const supabase = getSupabase(env);
  const { error } = await supabase
    .from('user_roles')
    .upsert([{ telegram_id: telegramId, role: 'coworker' }]);

  if (error) throw new ApiError(500, 'database_error', `Failed to promote: ${error.message}`);
}

export async function updateMatch(env: Env, matchId: string, data: { score_player1: number, score_player2: number, winner_id: string, loser_id: string }): Promise<void> {
  const supabase = getSupabase(env);

  const { error: matchErr } = await supabase
    .from('matches')
    .update({
      score_player1: data.score_player1,
      score_player2: data.score_player2,
      winner_id: data.winner_id,
      loser_id: data.loser_id,
      status: 'completed',
      completed_at: new Date().toISOString()
    })
    .eq('id', matchId);

  if (matchErr) throw new ApiError(500, 'database_error', `Failed to update match: ${matchErr.message}`);

  const { data: winner } = await supabase.from('players').select('wins').eq('id', data.winner_id).single();
  const { data: loser } = await supabase.from('players').select('losses').eq('id', data.loser_id).single();

  if (winner) {
    await supabase.from('players').update({ wins: winner.wins + 1, status: 'active' }).eq('id', data.winner_id);
  }

  if (loser) {
    const newLosses = loser.losses + 1;
    const status = newLosses >= 2 ? 'eliminated' : 'active';
    await supabase.from('players').update({ losses: newLosses, status }).eq('id', data.loser_id);
  }

  const { data: matchData } = await supabase.from('matches').select('system_id').eq('id', matchId).single();
  if (matchData) {
    await advanceBracket(env, matchData.system_id);
  }
}

async function advanceBracket(env: Env, systemId: string) {
  const supabase = getSupabase(env);

  const { data: alivePlayers } = await supabase
    .from('players')
    .select('*')
    .eq('system_id', systemId)
    .neq('status', 'eliminated');

  if (!alivePlayers) return;

  if (alivePlayers.length === 1) {
    await supabase.from('systems').update({ status: 'completed' }).eq('id', systemId);
    return;
  }

  const { data: allMatches } = await supabase.from('matches').select('*').eq('system_id', systemId);
  const busyPlayers = new Set<string>();

  let maxWinRound = 0;
  let maxLoseRound = 0;

  allMatches?.forEach(m => {
    if (['ready', 'pending', 'active'].includes(m.status)) {
      if (m.player1_id) busyPlayers.add(m.player1_id);
      if (m.player2_id) busyPlayers.add(m.player2_id);
    }
    if (m.bracket === 'winners' && m.round > maxWinRound) maxWinRound = m.round;
    if (m.bracket === 'losers' && m.round > maxLoseRound) maxLoseRound = m.round;
  });

  const available = alivePlayers.filter(p => !busyPlayers.has(p.id));
  const winners = available.filter(p => p.losses === 0);
  const losers = available.filter(p => p.losses === 1);
  const newMatches = [];

  if (alivePlayers.length === 2 && available.length === 2) {
    if (winners.length === 1 && losers.length === 1) {
      newMatches.push({
        system_id: systemId, bracket: 'grand_final', round: 1, position: 1,
        status: 'ready', player1_id: winners[0].id, player2_id: losers[0].id
      });
      winners.length = 0; losers.length = 0;
    } else if (losers.length === 2) {
      newMatches.push({
        system_id: systemId, bracket: 'grand_final', round: 2, position: 1,
        status: 'ready', player1_id: losers[0].id, player2_id: losers[1].id
      });
      losers.length = 0;
    }
  }

  for (let i = 0; i < winners.length - 1; i += 2) {
    newMatches.push({
      system_id: systemId, bracket: 'winners', round: maxWinRound + 1, position: i + 1,
      status: 'ready', player1_id: winners[i].id, player2_id: winners[i+1].id
    });
  }

  for (let i = 0; i < losers.length - 1; i += 2) {
    newMatches.push({
      system_id: systemId, bracket: 'losers', round: maxLoseRound + 1, position: i + 1,
      status: 'ready', player1_id: losers[i].id, player2_id: losers[i+1].id
    });
  }

  if (newMatches.length > 0) {
    await supabase.from('matches').insert(newMatches);
  }
}

export async function buildSystemList(env: Env): Promise<SystemListPayload> {
  const [systems, playerCounts, matchCounts] = await Promise.all([listSystems(env), playerCountsBySystem(env), matchCountsBySystem(env)]);
  const items = [...systems].sort((left, right) => (right.created_at ?? '').localeCompare(left.created_at ?? '')).map((system) => {
      const matches = matchCounts.get(system.id) ?? { total: 0, ready: 0 };
      return {
        id: system.id, name: system.name, description: system.description ?? '', status: system.status,
        registration_open: system.registration_open, max_players: system.max_players,
        player_count: playerCounts.get(system.id) ?? 0, match_count: matches.total, ready_count: matches.ready,
        created_at: system.created_at, updated_at: system.updated_at,
      };
    });
  return { schema: API_SCHEMA_VERSION, systems: items };
}

export async function buildDashboard(env: Env, systemId: string): Promise<DashboardPayload | null> {
  const system = await fetchOne<SystemRow>(getSupabase(env), 'systems', 'id', systemId);
  if (!system) return null;
  const [players, matches] = await Promise.all([listPlayers(env, system.id), listMatches(env, system.id)]);
  const playersById = new Map(players.map((player) => [player.id, player]));
  const orderedMatches = [...matches].sort((left, right) => left.round - right.round || left.position - right.position);

  const brackets: DashboardPayload['brackets'] = { winners: [], losers: [], grand_final: [] };
  for (const match of orderedMatches) {
    const payload = serializeMatch(match, playersById, matches);
    if (match.bracket === 'winners') brackets.winners.push(payload);
    else if (match.bracket === 'losers') brackets.losers.push(payload);
    else if (match.bracket === 'grand_final') brackets.grand_final.push(payload);
  }

  const alive = players.filter((player) => ALIVE.has(player.status));
  const champion = system.status === 'completed' && alive.length === 1 ? alive[0] : null;

  return {
    schema: API_SCHEMA_VERSION, generated_at: new Date().toISOString(),
    system: {
      id: system.id,
      name: system.name,
      description: system.description ?? '',
      announcement: system.announcement ?? null, // 📢 خطای تایپ برطرف شد
      status: system.status,
      registration_open: system.registration_open,
      max_players: system.max_players,
      player_count: players.length,
      created_at: system.created_at,
      updated_at: system.updated_at
    },
    champion: playerRef(champion), players: orderPlayers(players).map(playerSummary), brackets,
    counts: { players: players.length, matches: matches.length, completed: matches.filter((match) => match.status === 'completed').length, ready: matches.filter((match) => match.status === 'ready').length, active: matches.filter((match) => match.status === 'active').length, byes: matches.filter((match) => match.is_bye).length, remaining: alive.length },
  };
}