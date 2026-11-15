/**
 * Bracket presentation helpers.
 *
 * Only the reachability analysis is ported here: results are still recorded
 * by the admin CLI, so the Worker is a read-only projection. This mirrors
 * `core/bracket.py::is_match_dead`, including the padded-bracket rules, so
 * unreachable losers-bracket slots stay hidden in the mini-app exactly as
 * they did in the Python dashboard.
 */

export interface BracketMatch {
  id: string;
  bracket: string;
  round: number;
  position: number;
  player1_id: string | null;
  player2_id: string | null;
  next_winner_match_id: string | null;
  next_loser_match_id: string | null;
}

/** Return the target slot (0 or 1) that a feeder participant occupies. */
function slotFor(
  source: BracketMatch,
  target: BracketMatch,
  isLoser: boolean,
): number {
  if (target.bracket === 'winners') {
    // Winners of (round, position) feed (round + 1, position // 2).
    return source.position % 2;
  }

  if (target.bracket === 'losers') {
    if (source.bracket === 'winners') {
      // Round 1 pairs dropped losers two by two; later rounds enter on slot 1.
      return source.round === 1 ? source.position % 2 : 1;
    }
    // Minor (odd) losers rounds feed slot 0, major rounds use parity.
    return source.round % 2 === 1 ? 0 : source.position % 2;
  }

  // Grand final: winners champion owns slot 0, losers representative slot 1.
  if (source.bracket === 'winners') {
    return isLoser ? 1 : 0;
  }
  return 1;
}

function findFeeder(
  matches: readonly BracketMatch[],
  target: BracketMatch,
  slot: number,
): [BracketMatch | null, boolean] {
  for (const source of matches) {
    if (
      source.next_winner_match_id === target.id &&
      slotFor(source, target, false) === slot
    ) {
      return [source, false];
    }
    if (
      source.next_loser_match_id === target.id &&
      slotFor(source, target, true) === slot
    ) {
      return [source, true];
    }
  }
  return [null, false];
}

function inputDead(
  matches: readonly BracketMatch[],
  match: BracketMatch,
  slot: number,
  visiting: Set<string>,
): boolean {
  const key = `${match.id}:${slot}`;
  if (visiting.has(key)) {
    // A cycle cannot be produced by build_bracket; treat it as unreachable so
    // malformed data can never hang the request.
    return true;
  }

  const current = slot === 0 ? match.player1_id : match.player2_id;
  if (current !== null) {
    return false;
  }

  visiting.add(key);
  try {
    const [source, feedsLoser] = findFeeder(matches, match, slot);
    if (source === null) {
      return true;
    }
    return feedsLoser
      ? !matchLive(matches, source, visiting)
      : matchDead(matches, source, visiting);
  } finally {
    visiting.delete(key);
  }
}

function matchDead(
  matches: readonly BracketMatch[],
  match: BracketMatch,
  visiting: Set<string>,
): boolean {
  return (
    inputDead(matches, match, 0, visiting) &&
    inputDead(matches, match, 1, visiting)
  );
}

function matchLive(
  matches: readonly BracketMatch[],
  match: BracketMatch,
  visiting: Set<string>,
): boolean {
  return (
    !inputDead(matches, match, 0, visiting) &&
    !inputDead(matches, match, 1, visiting)
  );
}

/**
 * Return `true` when a padded-bracket match can never be played.
 *
 * Such matches stay `pending` forever; presentation layers hide them.
 */
export function isMatchDead(
  matches: readonly BracketMatch[],
  match: BracketMatch,
): boolean {
  return matchDead(matches, match, new Set());
}
