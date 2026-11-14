"""Double-elimination bracket progression engine for TourneyCore.

The engine is deliberately framework independent: it takes plain lists of
:class:`~core.models.Match` and :class:`~core.models.Player` objects, mutates
them in memory and returns the matches that were touched. Persisting the
result is the caller's job (normally through
:class:`~core.storage.StorageManager`).

Bracket graph
-------------
Every match stores two forwarding pointers:

* ``next_winner_match_id`` - the match the winner advances to.
* ``next_loser_match_id`` - the match the loser drops into, or ``None`` when a
  loss means elimination.

Because the graph is stored explicitly, a tournament can be rebuilt after a
restart without recomputing the whole tree. :func:`build_bracket` writes those
pointers once; :func:`advance_match` only follows them.

Grand final rules
-----------------
``GRAND_FINAL`` round 1 is the first final: winners-bracket champion (slot 1)
against the losers-bracket champion (slot 2). If the winners-bracket champion
wins, the tournament ends. If the losers-bracket champion wins, both players
now have one loss and the round-2 reset match decides the title.

Exported symbols
----------------
``advance_match``
    Record a result and start the follow-up matches.
``build_bracket``
    Create the full match graph for a set of players.
``find_next_match``
    Locate the next playable match for a player.
``is_tournament_over``
    Decide whether a single player is left standing.
``make_grand_final``
    Ensure the final matches exist and are seeded.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from core.models import (
    BracketSide,
    Match,
    MatchStatus,
    Player,
    PlayerStatus,
    WinMethod,
)

__all__ = [
    "advance_match",
    "build_bracket",
    "find_next_match",
    "is_tournament_over",
    "make_grand_final",
]

#: Match states that still expect a result.
_OPEN_STATUSES = (MatchStatus.PENDING, MatchStatus.READY)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _index(matches: Iterable[Match]) -> dict[str, Match]:
    """Return a lookup table keyed by match id."""

    return {match.id: match for match in matches}


def _require(index: dict[str, Match], match_id: str) -> Match:
    """Return the match with ``match_id`` or raise a helpful error."""

    try:
        return index[match_id]
    except KeyError:
        raise ValueError(f"Match {match_id!r} not found in this tournament.") from None


def _slot_for(source: Match, target: Match, *, is_loser: bool) -> int:
    """Return the target slot (0 or 1) that a participant must occupy.

    The mapping is derived from the canonical double-elimination layout that
    :func:`build_bracket` produces, so the two functions must stay in sync.
    """

    if target.bracket is BracketSide.WINNERS:
        # Winners of (round, position) feed (round + 1, position // 2):
        # even positions take slot 0, odd positions take slot 1.
        return source.position % 2

    if target.bracket is BracketSide.LOSERS:
        if source.bracket is BracketSide.WINNERS:
            # A winners-bracket loser drops in: round 1 pairs losers two by
            # two (slot = position parity), later rounds enter the "major"
            # losers round on the second slot.
            if source.round == 1:
                return source.position % 2
            return 1
        # Losers-bracket winner moving up. Odd ("minor") rounds feed the
        # following major round on slot 0; even ("major") rounds feed the
        # next minor round using position parity.
        if source.round % 2 == 1:
            return 0
        return source.position % 2

    # Grand final: the winners-bracket champion owns slot 0, the
    # losers-bracket representative owns slot 1.
    if source.bracket is BracketSide.WINNERS:
        return 1 if is_loser else 0
    return 1


def _offer(target: Match, player_id: str | None, slot: int) -> None:
    """Place ``player_id`` into ``slot`` and mark the target ready if full."""

    if not player_id:
        return

    if slot == 0:
        if target.player1_id not in (None, player_id):
            raise ValueError(
                f"Match {target.id!r} already has a different player in slot 0."
            )
        target.player1_id = player_id
    else:
        if target.player2_id not in (None, player_id):
            raise ValueError(
                f"Match {target.id!r} already has a different player in slot 1."
            )
        target.player2_id = player_id

    if target.status is MatchStatus.PENDING and target.has_both_players:
        target.status = MatchStatus.READY


def _seed_order(size: int) -> list[int]:
    """Return the standard 1-based seeding order for a bracket of ``size``.

    Example: ``size == 8`` returns ``[1, 8, 4, 5, 2, 7, 3, 6]`` so that seed 1
    plays seed 8, seed 4 plays seed 5, and so on.
    """

    order = [1]
    while len(order) < size:
        block = len(order) * 2
        expanded: list[int] = []
        for seed in order:
            expanded.append(seed)
            expanded.append(block + 1 - seed)
        order = expanded
    return order


def _update_players(
    players: Sequence[Player] | None,
    winner_id: str,
    loser_id: str,
) -> None:
    """Apply the win/loss counters and elimination rule to player records."""

    if players is None:
        return

    by_id = {player.id: player for player in players}
    winner = by_id.get(winner_id)
    loser = by_id.get(loser_id)
    if winner is not None:
        winner.register_win()
    if loser is not None:
        loser.register_loss()


def _advance_grand_final(index: dict[str, Match], match: Match) -> tuple[Match | None, Match | None]:
    """Handle a completed grand-final match.

    A round-1 final only needs a rematch when the losers-bracket champion wins,
    because that result hands the winners-bracket champion their first loss.
    """

    if match.round != 1 or match.next_winner_match_id is None:
        return None, None

    reset = index.get(match.next_winner_match_id)
    if reset is None:
        return None, None

    # Slot 0 always holds the winners-bracket champion. If they won the first
    # final the title is decided and the reset match is never played.
    if match.winner_id == match.player1_id:
        return None, None

    if match.player1_id is None or match.player2_id is None:
        return None, None

    reset.assign_players(match.player1_id, match.player2_id)
    return reset, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_bracket(system_id: str, player_ids: Sequence[str]) -> list[Match]:
    """Create the full double-elimination match graph for ``player_ids``.

    Players are seeded in the order given (first element is seed 1). The
    function returns every match of the tournament, already linked through
    ``next_winner_match_id`` / ``next_loser_match_id``. First-round matches are
    populated and marked ``READY``; later matches fill in as results arrive.

    Args:
        system_id: Identifier of the owning gaming system.
        player_ids: Two or more unique player identifiers.

    Returns:
        A list of :class:`~core.models.Match` objects.

    Raises:
        ValueError: If fewer than two players are supplied, identifiers are not
            unique, or the count is not a power of two.

    Note:
        Non power-of-two fields require bye scheduling, which is not part of
        this engine yet. Pad the roster to 2, 4, 8, 16... players or schedule
        explicit walkovers before calling this function.
    """

    roster = list(player_ids)
    if len(roster) < 2:
        raise ValueError("A double-elimination bracket needs at least two players.")
    if len(set(roster)) != len(roster):
        raise ValueError("Player identifiers must be unique.")

    size = len(roster)
    if size & (size - 1):
        raise ValueError(
            "build_bracket requires a power-of-two player count "
            f"(got {size}). Pad the roster or schedule byes first."
        )

    rounds = int(math.log2(size))
    matches: list[Match] = []
    winners: dict[tuple[int, int], Match] = {}
    losers: dict[tuple[int, int], Match] = {}

    def new_match(bracket: BracketSide, round_no: int, position: int) -> Match:
        match = Match(
            system_id=system_id,
            bracket=bracket,
            round=round_no,
            position=position,
        )
        matches.append(match)
        return match

    # --- grand final slots (created first so links can point at them) ------
    grand_final_1 = new_match(BracketSide.GRAND_FINAL, 1, 0)
    grand_final_2 = new_match(BracketSide.GRAND_FINAL, 2, 0)
    grand_final_1.next_winner_match_id = grand_final_2.id
    grand_final_2.next_winner_match_id = None
    grand_final_2.next_loser_match_id = None

    # --- winners bracket ---------------------------------------------------
    for round_no in range(1, rounds + 1):
        count = size // (2**round_no)
        for position in range(count):
            winners[(round_no, position)] = new_match(
                BracketSide.WINNERS, round_no, position
            )

    # --- losers bracket ----------------------------------------------------
    for level in range(1, rounds):
        count = size // (2 ** (level + 1))
        for position in range(count):
            losers[(2 * level - 1, position)] = new_match(
                BracketSide.LOSERS, 2 * level - 1, position
            )
            losers[(2 * level, position)] = new_match(
                BracketSide.LOSERS, 2 * level, position
            )

    # --- link the winners bracket -----------------------------------------
    for round_no in range(1, rounds + 1):
        count = size // (2**round_no)
        for position in range(count):
            match = winners[(round_no, position)]

            if round_no < rounds:
                match.next_winner_match_id = winners[(round_no + 1, position // 2)].id
            else:
                match.next_winner_match_id = grand_final_1.id

            if rounds == 1:
                # Two-player event: the opening match is also the winners
                # final, so the loser still earns a place in the grand final.
                match.next_loser_match_id = grand_final_1.id
            elif round_no == 1:
                match.next_loser_match_id = losers[(1, position // 2)].id
            else:
                match.next_loser_match_id = losers[(2 * (round_no - 1), position)].id

    # --- link the losers bracket ------------------------------------------
    for level in range(1, rounds):
        count = size // (2 ** (level + 1))
        minor = 2 * level - 1
        major = 2 * level
        for position in range(count):
            minor_match = losers[(minor, position)]
            major_match = losers[(major, position)]

            minor_match.next_winner_match_id = major_match.id
            minor_match.next_loser_match_id = None
            major_match.next_loser_match_id = None

            if level < rounds - 1:
                major_match.next_winner_match_id = losers[
                    (major + 1, position // 2)
                ].id
            else:
                major_match.next_winner_match_id = grand_final_1.id

    # --- seed the first winners round -------------------------------------
    slots: list[str | None] = [None] * size
    order = _seed_order(size)
    for position, seed in enumerate(order):
        slots[position] = roster[seed - 1]

    for position in range(size // 2):
        winners[(1, position)].assign_players(
            slots[2 * position], slots[2 * position + 1]
        )

    return matches


def advance_match(
    matches: list[Match],
    match_id: str,
    winner_id: str,
    *,
    method: WinMethod = WinMethod.NORMAL,
    score_player1: int | None = None,
    score_player2: int | None = None,
    players: Sequence[Player] | None = None,
) -> tuple[Match | None, Match | None]:
    """Record the result of ``match_id`` and seed the follow-up matches.

    Args:
        matches: Every match of the tournament (modified in place).
        match_id: Identifier of the match that just finished.
        winner_id: Player who won; must be one of the two participants.
        method: How the result was decided (normal play or walkover).
        score_player1: Optional score for the first slot.
        score_player2: Optional score for the second slot.
        players: Optional player records. When supplied, win/loss counters are
            updated and a second loss eliminates the player.

    Returns:
        ``(next_winner_match, next_loser_match)`` - the matches that received a
        participant, or ``None`` where no such match exists. Both targets are
        marked ``READY`` once their two slots are filled.

    Raises:
        ValueError: If the match is unknown, already completed, or ``winner_id``
            is not a participant.
    """

    index = _index(matches)
    match = _require(index, match_id)

    if match.status is MatchStatus.COMPLETED:
        raise ValueError(f"Match {match_id!r} is already completed.")
    if match.player1_id is None and match.player2_id is None:
        raise ValueError(f"Match {match_id!r} has no players assigned.")

    # ``Match.complete`` performs all validation after the whole result has
    # been assembled, which avoids tripping Pydantic's assignment validators
    # with a half-written state.
    match.complete(winner_id, method, score_player1, score_player2)

    if match.loser_id is not None:
        _update_players(players, winner_id, match.loser_id)

    if match.bracket is BracketSide.GRAND_FINAL:
        return _advance_grand_final(index, match)

    next_winner: Match | None = None
    if match.next_winner_match_id is not None:
        next_winner = _require(index, match.next_winner_match_id)
        _offer(next_winner, winner_id, _slot_for(match, next_winner, is_loser=False))

    next_loser: Match | None = None
    if match.next_loser_match_id is not None:
        next_loser = _require(index, match.next_loser_match_id)
        _offer(
            next_loser,
            match.loser_id,
            _slot_for(match, next_loser, is_loser=True),
        )

    return next_winner, next_loser


def find_next_match(
    matches: Iterable[Match],
    player_id: str,
    *,
    side: BracketSide | None = None,
) -> Match | None:
    """Return the next playable match that involves ``player_id``.

    Matches are ordered by bracket (winners before losers before grand final)
    and then by round and position.

    Args:
        matches: Every match of the tournament.
        player_id: Player to look for.
        side: Restrict the search to one bracket side when provided.

    Returns:
        The first ``PENDING`` or ``READY`` match containing the player, or
        ``None`` when the player has no upcoming bout.
    """

    order = {
        BracketSide.WINNERS: 0,
        BracketSide.LOSERS: 1,
        BracketSide.GRAND_FINAL: 2,
    }

    candidates = [
        match
        for match in matches
        if match.status in _OPEN_STATUSES
        and (side is None or match.bracket is side)
        and player_id in (match.player1_id, match.player2_id)
    ]
    if not candidates:
        return None

    return min(
        candidates,
        key=lambda match: (order[match.bracket], match.round, match.position),
    )


def is_tournament_over(
    matches: Iterable[Match],
    players: Sequence[Player],
) -> bool:
    """Return ``True`` when at most one player can still win the title.

    Players that are eliminated or withdrawn no longer count. ``matches`` is
    accepted for API symmetry and for future rules that also require the final
    match to be completed.
    """

    del matches  # reserved for future rules
    active = [
        player
        for player in players
        if player.status
        not in (PlayerStatus.ELIMINATED, PlayerStatus.WITHDRAWN)
    ]
    return len(active) <= 1


def make_grand_final(
    matches: list[Match],
    system_id: str,
    winner_wb_id: str,
    player_lb_id: str | None,
) -> tuple[Match, Match | None]:
    """Ensure the grand-final slots exist and seed them.

    Args:
        matches: Every match of the tournament (modified in place).
        system_id: Identifier of the owning gaming system.
        winner_wb_id: Winners-bracket champion.
        player_lb_id: Losers-bracket champion, or ``None`` when the losers
            bracket produced nobody.

    Returns:
        ``(first_final, reset_final)``. ``reset_final`` is ``None`` when no
        losers-bracket player is available, because the title is already
        decided.
    """

    index = _index(matches)

    first = next(
        (
            match
            for match in matches
            if match.bracket is BracketSide.GRAND_FINAL and match.round == 1
        ),
        None,
    )
    if first is None:
        first = Match(
            system_id=system_id,
            bracket=BracketSide.GRAND_FINAL,
            round=1,
            position=0,
        )
        matches.append(first)

    if player_lb_id is None:
        first.assign_players(winner_wb_id, None)
        first.is_bye = True
        return first, None

    first.assign_players(winner_wb_id, player_lb_id)

    reset = next(
        (
            match
            for match in matches
            if match.bracket is BracketSide.GRAND_FINAL and match.round == 2
        ),
        None,
    )
    if reset is None:
        reset = Match(
            system_id=system_id,
            bracket=BracketSide.GRAND_FINAL,
            round=2,
            position=0,
        )
        matches.append(reset)
        first.next_winner_match_id = reset.id

    return first, reset
