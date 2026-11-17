import { isMatchDead } from '../lib/bracket';
import type { MatchRow, PlayerRow } from '../types';

/** Human-readable bracket labels, matching the old aiogram helpers. */
const BRACKET_LABELS: Record<string, string> = {
  winners: 'Winners bracket',
  losers: 'Losers bracket',
  grand_final: 'Grand final',
};

const BRACKET_ORDER: Record<string, number> = {
  winners: 0,
  losers: 1,
  grand_final: 2,
};

const OPEN_STATUSES = new Set(['pending', 'ready']);

/** Welcome text reused by `/start` and `/help`. */
export const WELCOME_TEXT =
  '👋 Welcome to TourneyCore!\n\n' +
  'Commands:\n' +
  '/register CODE — redeem the entry code from your admin\n' +
  '/me — your profile and record\n' +
  '/next — your next match\n' +
  '/bracket — the full tournament bracket\n' +
  '/standings — current standings\n' +
  '/app — open the live bracket mini-app\n' +
  '/help — show this message\n\n' +
  'You can also send your 8-character entry code directly.';

export function playerName(
  players: readonly PlayerRow[],
  playerId: string | null,
): string {
  if (playerId === null) {
    return 'TBD';
  }
  return players.find((player) => player.id === playerId)?.name ?? playerId;
}

/** Format the profile card shown by `/me`. */
export function formatProfile(player: PlayerRow): string {
  return (
    `👤 ${player.name}\n` +
    `Status: ${player.status}\n` +
    `Record: ${player.wins}W - ${player.losses}L\n` +
    `Player id: ${player.id}`
  );
}

function formatMatch(match: MatchRow, players: readonly PlayerRow[]): string {
  const left = playerName(players, match.player1_id);
  const right = playerName(players, match.player2_id);
  const side = BRACKET_LABELS[match.bracket] ?? match.bracket;

  if (match.status === 'completed' && match.is_bye) {
    return `${side} R${match.round}: bye for ${playerName(players, match.winner_id)}`;
  }
  if (match.status === 'completed') {
    const score =
      match.score_player1 !== null || match.score_player2 !== null
        ? ` (${match.score_player1 ?? '–'}-${match.score_player2 ?? '–'})`
        : '';
    return `${side} R${match.round}: ${left} vs ${right} — winner ${playerName(players, match.winner_id)}${score}`;
  }
  return `${side} R${match.round}: ${left} vs ${right} [${match.status}]`;
}

/** Format the next scheduled match for one player. */
export function formatNextMatch(
  match: MatchRow | null,
  players: readonly PlayerRow[],
  playerId: string | null,
): string {
  if (match === null) {
    return 'You have no upcoming match right now.';
  }

  let opponentId: string | null = null;
  if (playerId !== null) {
    opponentId =
      match.player1_id === playerId ? match.player2_id : match.player1_id;
  }

  const side = BRACKET_LABELS[match.bracket] ?? match.bracket;
  return (
    `Your next match: ${playerName(players, opponentId)}\n` +
    `${side}, round ${match.round}\n` +
    `Status: ${match.status}`
  );
}

/** Render the whole bracket as a plain-text message. */
export function formatBracket(
  players: readonly PlayerRow[],
  matches: readonly MatchRow[],
): string {
  if (matches.length === 0) {
    return 'The bracket has not been seeded yet.';
  }

  const visible = matches.filter(
    (match) => !(match.status === 'pending' && isMatchDead(matches, match)),
  );

  const lines: string[] = [];
  for (const side of ['winners', 'losers', 'grand_final']) {
    const group = visible
      .filter((match) => match.bracket === side)
      .sort(
        (left, right) => left.round - right.round || left.position - right.position,
      );
    if (group.length === 0) {
      continue;
    }
    lines.push(`— ${BRACKET_LABELS[side]} —`);
    for (const match of group) {
      lines.push(formatMatch(match, players));
    }
  }
  return lines.join('\n');
}

/** Render the roster ordered by losses, then wins, then name. */
export function formatStandings(players: readonly PlayerRow[]): string {
  if (players.length === 0) {
    return 'No players registered yet.';
  }

  const ordered = [...players].sort(
    (left, right) =>
      left.losses - right.losses ||
      right.wins - left.wins ||
      left.name.localeCompare(right.name),
  );

  const lines = ['Standings:'];
  ordered.forEach((player, index) => {
    let mark = '';
    if (player.status === 'eliminated') {
      mark = ' (out)';
    } else if (player.status === 'withdrawn') {
      mark = ' (withdrawn)';
    }
    lines.push(
      `${index + 1}. ${player.name} — ${player.wins}W-${player.losses}L${mark}`,
    );
  });
  return lines.join('\n');
}

/** Return the next playable match that involves `playerId`. */
export function findNextMatch(
  matches: readonly MatchRow[],
  playerId: string,
): MatchRow | null {
  const candidates = matches.filter(
    (match) =>
      OPEN_STATUSES.has(match.status) &&
      (match.player1_id === playerId || match.player2_id === playerId),
  );

  if (candidates.length === 0) {
    return null;
  }

  const ordered = [...candidates].sort(
    (left, right) =>
      (BRACKET_ORDER[left.bracket] ?? 99) - (BRACKET_ORDER[right.bracket] ?? 99) ||
      left.round - right.round ||
      left.position - right.position,
  );

  return ordered[0] ?? null;
}