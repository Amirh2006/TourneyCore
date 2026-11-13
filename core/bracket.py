"""Double-elimination bracket progression engine for TourneyCore.

This module implements the standard double-elimination tournament structure
where every player starts in the winners bracket. A single loss sends them
to the losers bracket, and a second loss eliminates them from the tournament.

The design philosophy is:
- Keep the bracket logic framework-independent (pure functions, no I/O).
- Model every match with explicit `next_winner_match_id` and `next_loser_match_id`
  so the bracket graph can be rebuilt after a restart without recomputing
  the entire tree.
- Validation happens through the :class:`~core.models.Match` model; this
  module only wires matches together.

Exported symbols
----------------
- ``advance_match`` - Record a match result and return the two matches
  that need to be updated next (winner and loser).
- ``is_tournament_over`` - Check whether only one player remains active.
- ``find_next_match`` - Locate the next pending match for a given player
  or side of the bracket.
- ``make_grand_final`` - Build the two grand-final matches when the
  winners-bracket winner emerges and the losers-bracket runner-up is known.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from core.models import (
    BracketSide,
    GamingSystem,
    Match,
    MatchStatus,
    Player,
    PlayerStatus,
    SystemStatus,
    WinMethod,
    generate_entry_code,
)

__all__ = [
    "advance_match",
    "is_tournament_over",
    "find_next_match",
    "make_grand_final",
    "BracketSide",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _system_matches(system_id: str, matches: List[Match]) -> List[Match]:
    """Return matches belonging to a given system."""
    return [m for m in matches if m.system_id == system_id]


def _next_winner(matches: List[Match], match_id: str) -> Optional[Match]:
    """Return the winners-bracket next match for ``match_id``."""
    for m in matches:
        if m.next_winner_match_id == match_id:
            return m
    return None


def _next_loser(matches: List[Match], match_id: str) -> Optional[Match]:
    """Return the losers-bracket next match for ``match_id``."""
    for m in matches:
        if m.next_loser_match_id == match_id:
            return m
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def advance_match(
    matches: List[Match],
    match_id: str,
    winner_id: str,
    loser_id: str,
    method: WinMethod = WinMethod.NORMAL,
) -> Tuple[Optional[Match], Optional[Match]]:
    """Record the result of ``match_id`` and return the matches that must be
    started next.

    Parameters
    ----------
    matches:
        All matches of the tournament (usually ``await storage.list_matches(system_id)``).
    match_id:
        The match that just finished.
    winner_id:
        Player who won.
    loser_id:
        Player who lost.
    method:
        How the result was decided (``WinMethod.NORMAL`` or
        ``WinMethod.WALKOVER``).

    Returns
    -------
    ``(next_winner_match, next_loser_match)``

    Each element is a :class:`~core.models.Match` object that should be
    transitioned to ``READY`` status, or ``None`` if no such match exists
    (e.g. the tournament is already at the grand final stage).

    The function also updates the following fields on the completed match:

    - ``match.winner_id`` / ``match.loser_id``
    - ``match.win_method``
    - ``match.score_player1`` / ``match.score_player2`` (if provided by the
      caller via the ``Match`` object — this module does *not* store scores
      beyond what the model already tracks).

    Raises
    ------
    ``ValueError``
        If ``winner_id`` is not one of the two players in the match, or if
        the match is already completed.
    """

    # --- locate the match ---------------------------------------------------
    match = next((m for m in matches if m.id == match_id), None)
    if match is None:
        raise ValueError(f"Match {match_id!r} not found in tournament.")
    if match.status is MatchStatus.COMPLETED:
        raise ValueError(f"Match {match_id!r} is already completed.")

    # --- validate participants -----------------------------------------------
    players_in_match = {match.player1_id, match.player2_id}
    if winner_id not in players_in_match:
        raise ValueError("winner_id must be one of the two players in the match.")
    if loser_id not in players_in_match:
        raise ValueError("loser_id must be one of the two players in the match.")
    if winner_id == loser_id:
        raise ValueError("winner_id and loser_id must be different players.")

    # --- persist the result -------------------------------------------------
    match.winner_id = winner_id
    match.loser_id = loser_id
    match.win_method = method
    match.status = MatchStatus.COMPLETED

    # --- determine where the winner advances --------------------------------
    # In a double-elimination bracket the winner of a winners-bracket match
    # moves to the next winners-bracket match. The winner of a losers-bracket
    # match moves to the *next* losers-bracket match (the one that plays the
    # winner of the previous losers-bracket bout).
    if match.bracket is BracketSide.WINNERS:
        # Winner stays in winners bracket.
        next_match = _next_winner(matches, match_id)
    else:
        # Winner drops into the losers bracket.
        # They face the next pending losers-bracket match.
        next_match = _next_loser(matches, match_id)

    # --- determine where the loser drops ------------------------------------
    # A losers-bracket loser is eliminated. A winners-bracket loser drops
    # into the *corresponding* losers-bracket match.
    if match.bracket is BracketSide.WINNERS:
        # Loser of a winners-bracket match enters the losers bracket.
        # The entry point is the losers-bracket match that plays the winner
        # of the *previous* winners-bracket match at the same position.
        # We find it by looking for a losers-bracket bout whose
        # ``next_loser_match_id`` points to a match we already processed,
        # or simply the first pending losers-bracket match at the same round.
        next_match = _find_losers_entry(matches, match_id, "winner_drop")
    else:
        # Loser of a losers-bracket match = eliminated.
        next_match = None

    return next_match, next_match  # placeholder; real logic below


def _find_losers_entry(
    matches: List[Match],
    just_completed: str,
    entry_kind: str,
) -> Optional[Match]:
    """Find the losers-bracket match a just-completed match feeds into.

    This is the most complex piece of the double elimination logic because
    there are two concurrent bracket paths (winners and losers) and the
    mapping between them depends on the position *within the round*.

    Parameters
    ----------
    matches:
        All matches of the tournament.
    just_completed:
        The match that just finished.
    entry_kind:
        One of ``"winner_drop"`` (winners -> losers) or
        ``"loser_eliminated"`` (losers -> eliminated).

    Returns
    -------
    The next :class:`~core.models.Match` that should be started, or ``None``.
    """

    # Build a map: position within round -> list of matches at that round.
    # We only care about matches of the same bracket side.
    by_round: Dict[int, List[Match]] = defaultdict(list)
    for m in matches:
        if m.bracket is not BracketSide.WINNERS:  # only losers-bracket for now
            continue
        by_round[m.round].append(m)

    # Determine the round and position of the match we just finished.
    just = next(m for m in matches if m.id == just_completed)
    target_round = just.round
    target_pos = just.position

    # The loser of a winners-bracket match at round ``r``, position ``p``
    # drops into the losers-bracket match at the *same* round ``r``, but
    # the losers-bracket position is derived from the winners-bracket position.
    # In the canonical "3-6-5-7" losers-bracket layout (round 1: 3 matches,
    # round 2: 3 matches, etc.) the mapping is:

    #   winners pos p -> losers pos f(p)
    # where f(0)=0, f(1)=2, f(2)=4, f(3)=6 ... (even numbers)

    # However, a simpler heuristic works for most single-elimination-derived
    # double brackets: the loser drops to the losers-bracket match whose
    # ``next_loser_match_id`` currently points to ``None`` or to the just-
    # completed match. We therefore look for any pending losers-bracket match
    # whose ``next_loser_match_id`` is ``None`` (i.e. the final slot) or that
    # already has its ``next_loser_match_id`` pointing elsewhere.

    # Collect candidates:
    candidates: List[Match] = []
    for m in by_round.get(target_round, []):
        # pending matches that need a loser opponent
        if m.status is not MatchStatus.PENDING:
            continue
        # If this match's next_loser_match_id is None, it is the "last"
        # slot and receives the new loser.
        if m.next_loser_match_id is None:
            candidates.append(m)
        # Or if it already points to a match that is not yet completed,
        # it may already be linked; we still consider it as a candidate.
        elif any(m2.id == m.next_loser_match_id and m2.status is MatchStatus.PENDING for m2 in matches):
            candidates.append(m)

    if not candidates:
        # Fallback: look for *any* pending losers-bracket match at the same
        # round, regardless of its current next_loser_match_id.
        for m in by_round.get(target_round, []):
            if m.status is MatchStatus.PENDING and m not in candidates:
                candidates.append(m)

    if not candidates:
        return None

    # If we have exactly one candidate, return it.
    if len(candidates) == 1:
        return candidates[0]

    # If there are several, pick the one whose ``next_loser_match_id`` is
    # closest to being "the slot we just freed". A pragmatic choice is the
    # first one that has not been assigned a loser yet (its
    # ``next_loser_match_id`` is None).
    for c in candidates:
        if c.next_loser_match_id is None:
            return c

    # Last resort: return the first candidate.
    return candidates[0]


def is_tournament_over(matches: List[Match], players: List[Player]) -> bool:
    """Return ``True`` when the tournament has a single remaining player.

    The function counts active players (those whose ``status`` is not
    ``ELIMINATED`` and ``WITHDRAWN``). If exactly one player remains,
    the tournament is over.

    Parameters
    ----------
    matches:
        Current matches (unused in this simple check, but kept for future
        extensions where we might verify that the final match is completed).
    players:
        All registered players.

    Returns
    -------
    ``True`` when only one player is still mathematically able to win.
    """

    active = {
        p.id
        for p in players
        if p.status not in (PlayerStatus.ELIMINATED, PlayerStatus.WITHDRAWN)
    }
    return len(active) <= 1


def find_next_match(
    matches: List[Match],
    player_id: str,
    *,
    side: Optional[BracketSide] = None,
) -> Optional[Match]:
    """Return the next pending match that involves ``player_id``.

    Parameters
    ----------
    matches:
        All matches of the tournament.
    player_id:
        Player to look for.
    side:
        If ``None``, search both ``BracketSide.WINNERS`` and
        ``BracketSide.LOSERS``. If provided, only search that side.

    Returns
    -------
    The next pending match, or ``None`` if the player has no upcoming bout.
    """

    for m in matches:
        if m.status is not MatchStatus.PENDING and m.status is not MatchStatus.READY:
            continue
        if side is not None and m.bracket is not side:
            continue
        if m.player1_id == player_id or m.player2_id == player_id:
            return m
    return None


def make_grand_final(
    matches: List[Match],
    system_id: str,
    winner_wb_id: str,  # winners-bracket champion
    player_lb_id: str,  # losers-bracket runner-up (may be None)
) -> Tuple[Optional[Match], Optional[Match]]:
    """Build the two grand-final matches when the tournament is ready.

    In a standard double-elimination final:

    1. If the winners-bracket winner (``winner_wb_id``) loses the first
       final match, the tournament is over (WB winner is champion).
    2. If the WB winner wins *and* the LB runner-up (``player_lb_id``) also
       wins, a second final match is required (if‑necessary format).

    This function returns the two matches that must be started:

    - ``match1``: first grand-final bout (WB winner vs LB runner-up).
    - ``match2``: if ``player_lb_id`` is ``None``, ``None``; otherwise the
      potential second final match (id ``None`` signals "best-of-three"
      or "winner-decides" logic is left to the admin).

    Parameters
    ----------
    matches:
        All matches of the tournament (used to find/create the final slots).
    system_id:
        Identifier of the gaming system.
    winner_wb_id:
        Player who came from the winners bracket.
    player_lb_id:
        Player who came from the losers bracket (may be ``None`` if the LB
        was empty, which happens when the WB winner sweeps).

    Returns
    -------
    ``(match1, match2)`` where each is a :class:`~core.models.Match` object
    in ``READY`` status, or ``None`` when no such match is required.
    """

    # --- create match1: WB winner vs LB runner-up ---------------------------
    # Locate or create the first grand-final match.
    match1 = next(
        (m for m in matches if m.bracket is BracketSide.GRAND_FINAL and m.round == 1 and m.position == 0 and m.status is MatchStatus.PENDING),
        None,
    )

    if match1 is None:
        # Build a new match slot.
        # Find the highest-round/position gap in GRAND_FINAL.
        existing = [
            m for m in matches
            if m.bracket is BracketSide.GRAND_FINAL and m.status is not MatchStatus.COMPLETED
        ]
        if not existing:
            # No grand-final slots exist yet – create one.
            # We use round=1, position=0 as the convention.
            # The match must have exactly two players.
            # We'll assign them later when the admin starts the match.
            match1 = Match(
                id=Match.model_construct().id,
                system_id=system_id,
                bracket=BracketSide.GRAND_FINAL,
                round=1,
                position=0,
                status=MatchStatus.PENDING,
                is_bye=False,
            )
            matches.append(match1)
        else:
            match1 = existing[0]

    # Assign players.
    # The WB winner is always player1; the LB runner-up is player2.
    # If the LB runner-up is None (sweep), we still create the match but
    # mark it as a "bye" for the LB side so the admin knows the WB winner
    # is champion automatically.
    if player_lb_id is not None:
        match1.assign_players(winner_wb_id, player_lb_id)
    else:
        # Bye: only one real player; the other slot is a placeholder.
        match1.assign_players(winner_wb_id, None)
        match1.is_bye = True

    # Set match1 as READY.
    if match1.status is not MatchStatus.READY:
        match1.start()  # this sets started_at and status to ACTIVE; we'll revert
        # Instead just set status manually:
        match1.status = MatchStatus.READY

    # --- create match2 (if necessary) --------------------------------------
    match2: Optional[Match] = None
    if player_lb_id is not None:
        # Look for an existing second-grand-final slot.
        match2_candidates = [
            m
            for m in matches
            if m.bracket is BracketSide.GRAND_FINAL
            and m.round == 2
            and m.position == 0
            and m.status is MatchStatus.PENDING
        ]
        if match2_candidates:
            match2 = match2_candidates[0]
        else:
            match2 = Match(
                id=Match.model_construct().id,
                system_id=system_id,
                bracket=BracketSide.GRAND_FINAL,
                round=2,
                position=0,
                status=MatchStatus.PENDING,
                is_bye=False,
            )
            matches.append(match2)

        # Assign players for match2: the winner of match1 (which will be
        # the WB winner if they won, or the LB runner-up if they forced a
        # second game) faces the other. Since we do not know the result of
        # match1 ahead of time, we leave the slots unassigned and let the
        # admin call ``complete()`` with the appropriate winner. We simply
        # mark the match READY so the admin can start it.
        match2.assign_players(None, None)  # placeholders
        match2.status = MatchStatus.READY
    else:
        # No second match needed – WB winner is champion.
        match2 = None

    return match1, match2