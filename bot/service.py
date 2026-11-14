"""Bot-facing service functions and text formatting.

Keeping these helpers free of aiogram types makes them trivially testable and
reusable by the web mini-app later.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.bracket import find_next_match, is_match_dead
from core.models import BracketSide, Match, MatchStatus, Player, PlayerStatus
from core.storage import StorageManager

#: Human readable labels for bracket sides.
BRACKET_LABELS: dict[BracketSide, str] = {
    BracketSide.WINNERS: "Winners bracket",
    BracketSide.LOSERS: "Losers bracket",
    BracketSide.GRAND_FINAL: "Grand final",
}


async def register_by_code(
    storage: StorageManager,
    entry_code: str,
    telegram_id: int,
    username: str | None = None,
) -> Player:
    """Redeem ``entry_code`` for the Telegram account that sent it."""

    return await storage.redeem_entry_code(entry_code, telegram_id, username)


async def load_player(
    storage: StorageManager, telegram_id: int
) -> Player | None:
    """Return the player linked to ``telegram_id``, if the account registered."""

    return await storage.find_player_by_telegram_id(telegram_id)


def player_name(players: Sequence[Player], player_id: str | None) -> str:
    """Resolve a player id to a display name."""

    if player_id is None:
        return "TBD"
    for player in players:
        if player.id == player_id:
            return player.name
    return player_id


def format_profile(player: Player) -> str:
    """Format the profile card shown by ``/me``."""

    status = player.status.value
    return (
        f"👤 {player.name}\n"
        f"Status: {status}\n"
        f"Record: {player.wins}W - {player.losses}L\n"
        f"Player id: {player.id}"
    )


def format_match(match: Match, players: Sequence[Player]) -> str:
    """Format a single match for a chat message."""

    left = player_name(players, match.player1_id)
    right = player_name(players, match.player2_id)
    side = BRACKET_LABELS.get(match.bracket, match.bracket.value)

    if match.status is MatchStatus.COMPLETED and match.is_bye:
        return f"{side} R{match.round}: bye for {player_name(players, match.winner_id)}"
    if match.status is MatchStatus.COMPLETED:
        score = ""
        if match.score_player1 is not None or match.score_player2 is not None:
            score = f" ({match.score_player1}-{match.score_player2})"
        return (
            f"{side} R{match.round}: {left} vs {right} — "
            f"winner {player_name(players, match.winner_id)}{score}"
        )
    return f"{side} R{match.round}: {left} vs {right} [{match.status.value}]"


def format_next_match(
    match: Match | None,
    players: Sequence[Player],
    *,
    player_id: str | None = None,
) -> str:
    """Format the next scheduled match for one player."""

    if match is None:
        return "You have no upcoming match right now."
    opponent_id = None
    if player_id is not None:
        opponent_id = (
            match.player2_id if match.player1_id == player_id else match.player1_id
        )
    opponent = player_name(players, opponent_id)
    side = BRACKET_LABELS.get(match.bracket, match.bracket.value)
    return (
        f"Your next match: {opponent}\n"
        f"{side}, round {match.round}\n"
        f"Status: {match.status.value}"
    )


def format_bracket(players: Sequence[Player], matches: Sequence[Match]) -> str:
    """Render the whole bracket as a plain-text message."""

    if not matches:
        return "The bracket has not been seeded yet."

    visible = [
        match
        for match in matches
        if not (match.status is MatchStatus.PENDING and is_match_dead(matches, match))
    ]

    lines: list[str] = []
    for side in (
        BracketSide.WINNERS,
        BracketSide.LOSERS,
        BracketSide.GRAND_FINAL,
    ):
        group = sorted(
            (match for match in visible if match.bracket is side),
            key=lambda match: (match.round, match.position),
        )
        if not group:
            continue
        lines.append(f"— {BRACKET_LABELS[side]} —")
        lines.extend(format_match(match, players) for match in group)
    return "\n".join(lines)


def format_standings(players: Sequence[Player]) -> str:
    """Render the roster ordered by losses, then wins, then name."""

    if not players:
        return "No players registered yet."

    ordered = sorted(players, key=lambda player: (player.losses, -player.wins, player.name))
    lines = ["Standings:"]
    for index, player in enumerate(ordered, start=1):
        mark = ""
        if player.status is PlayerStatus.ELIMINATED:
            mark = " (out)"
        elif player.status is PlayerStatus.WITHDRAWN:
            mark = " (withdrawn)"
        lines.append(
            f"{index}. {player.name} — {player.wins}W-{player.losses}L{mark}"
        )
    return "\n".join(lines)


def next_match_for(
    player: Player, players: Sequence[Player], matches: Sequence[Match]
) -> Match | None:
    """Convenience wrapper around :func:`core.bracket.find_next_match`."""

    del players  # kept for symmetry with the other helpers
    return find_next_match(matches, player.id)
