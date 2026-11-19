import { isMatchDead } from '../lib/bracket';
import type { MatchRow, PlayerRow } from '../types';

/** Human-readable Persian bracket labels for bot messages. */
const BRACKET_LABELS: Record<string, string> = {
  winners: 'براکت برندگان',
  losers: 'براکت بازندگان',
  grand_final: 'فینال بزرگ',
};

/** Human-readable Persian status labels for matches and players. */
const STATUS_LABELS: Record<string, string> = {
  pending: 'در انتظار',
  ready: 'آماده',
  active: 'در حال برگزاری',
  completed: 'پایان‌یافته',
  registered: 'ثبت‌نام‌شده',
  eliminated: 'حذف‌شده',
  withdrawn: 'انصراف‌داده',
  champion: 'قهرمان',
};

const BRACKET_ORDER: Record<string, number> = {
  winners: 0,
  losers: 1,
  grand_final: 2,
};

const OPEN_STATUSES = new Set(['pending', 'ready']);

/** Welcome text reused by `/start` and `/help`. */
export const WELCOME_TEXT =
  '👋 به ربات مدیریت تورنمنت TourneyCore خوش آمدید!\n\n' +
  'دستورات ربات:\n' +
  '/register کد — ثبت کد ورود دریافتی از مدیر\n' +
  '/me — مشاهده پروفایل و آمار شما\n' +
  '/next — مشاهده مسابقه بعدی شما\n' +
  '/bracket — نمایش کامل جدول مسابقات\n' +
  '/standings — نمایش جدول رده‌بندی\n' +
  '/app — باز کردن مینی‌اپ زنده مسابقات\n' +
  '/help — نمایش این راهنما\n\n' +
  'همچنین می‌توانید کد ورود ۸ کاراکتری خود را مستقیماً ارسال کنید.';

export function playerName(
  players: readonly PlayerRow[],
  playerId: string | null,
): string {
  if (playerId === null) {
    return 'نامشخص';
  }
  return players.find((player) => player.id === playerId)?.name ?? playerId;
}

/** Format the profile card shown by `/me`. */
export function formatProfile(player: PlayerRow): string {
  const statusLabel = STATUS_LABELS[player.status] ?? player.status;
  return (
    `👤 ${player.name}\n` +
    `وضعیت: ${statusLabel}\n` +
    `آمار: ${player.wins} برد - ${player.losses} باخت\n` +
    `شناسه بازیکن: ${player.id}`
  );
}

function formatMatch(match: MatchRow, players: readonly PlayerRow[]): string {
  const left = playerName(players, match.player1_id);
  const right = playerName(players, match.player2_id);
  const side = BRACKET_LABELS[match.bracket] ?? match.bracket;
  const statusLabel = STATUS_LABELS[match.status] ?? match.status;

  if (match.status === 'completed' && match.is_bye) {
    return `${side} - دور ${match.round}: استراحت (Bye) برای ${playerName(players, match.winner_id)}`;
  }
  if (match.status === 'completed') {
    const score =
      match.score_player1 !== null || match.score_player2 !== null
        ? ` (${match.score_player1 ?? '–'}-${match.score_player2 ?? '–'})`
        : '';
    return `${side} - دور ${match.round}: ${left} مقابل ${right} — برنده: ${playerName(players, match.winner_id)}${score}`;
  }
  return `${side} - دور ${match.round}: ${left} مقابل ${right} [${statusLabel}]`;
}

/** Format the next scheduled match for one player. */
export function formatNextMatch(
  match: MatchRow | null,
  players: readonly PlayerRow[],
  playerId: string | null,
): string {
  if (match === null) {
    return 'در حال حاضر مسابقه پیش‌رویی برای شما ثبت نشده است.';
  }

  let opponentId: string | null = null;
  if (playerId !== null) {
    opponentId =
      match.player1_id === playerId ? match.player2_id : match.player1_id;
  }

  const side = BRACKET_LABELS[match.bracket] ?? match.bracket;
  const statusLabel = STATUS_LABELS[match.status] ?? match.status;
  return (
    `مسابقه بعدی شما در برابر: ${playerName(players, opponentId)}\n` +
    `${side}، دور ${match.round}\n` +
    `وضعیت: ${statusLabel}`
  );
}

/** Render the whole bracket as a plain-text message. */
export function formatBracket(
  players: readonly PlayerRow[],
  matches: readonly MatchRow[],
): string {
  if (matches.length === 0) {
    return 'جدول مسابقات هنوز قرعه‌کشی نشده است.';
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
    return 'هنوز هیچ بازیکنی ثبت‌نام نکرده است.';
  }

  const ordered = [...players].sort(
    (left, right) =>
      left.losses - right.losses ||
      right.wins - left.wins ||
      left.name.localeCompare(right.name),
  );

  const lines = ['جدول رده‌بندی:'];
  ordered.forEach((player, index) => {
    let mark = '';
    if (player.status === 'eliminated') {
      mark = ' (حذف‌شده)';
    } else if (player.status === 'withdrawn') {
      mark = ' (انصراف‌داده)';
    }
    lines.push(
      `${index + 1}. ${player.name} — ${player.wins} برد، ${player.losses} باخت${mark}`,
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