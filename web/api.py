"""Read-only JSON payloads for the TourneyCore mini-app.

The web layer is a projection of the persisted tournament state. It never
mutates storage and never re-implements bracket rules:

* unreachable padded-bracket matches are flagged with
  :func:`core.bracket.is_match_dead`;
* standings use the same ordering as the Telegram bot (losses, then wins,
  then name).

Only display-safe fields leave the server. ``entry_code`` and ``telegram_id``
must never be serialized here: either value would let a visitor impersonate a
player or redeem their registration code.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from core.bracket import is_match_dead
from core.models import (
    BracketSide,
    Match,
    MatchStatus,
    Player,
    PlayerStatus,
    SystemStatus,
)
from core.storage import StorageManager

__all__ = [
    "API_SCHEMA_VERSION",
    "build_dashboard",
    "build_system_list",
    "serialize_match",
]

#: Bumped whenever the payload shape changes in a breaking way.
API_SCHEMA_VERSION = 1

#: Bracket sides in the order the frontend renders them.
BRACKETS: tuple[BracketSide, ...] = (
    BracketSide.WINNERS,
    BracketSide.LOSERS,
    BracketSide.GRAND_FINAL,
)

#: Player states that still take part in the tournament.
_ALIVE_STATES = (PlayerStatus.REGISTERED, PlayerStatus.ACTIVE)


def _isoformat(value: datetime | None) -> str | None:
    """Return an ISO-8601 string for ``value``, or ``None``."""

    return value.isoformat() if value is not None else None


def _player_ref(player: Player | None) -> dict[str, Any] | None:
    """Return the minimal player reference used inside match payloads."""

    if player is None:
        return None
    return {"id": player.id, "name": player.name, "seed": player.seed}


def _player_summary(player: Player) -> dict[str, Any]:
    """Return the public player record used by the standings table."""

    return {
        "id": player.id,
        "name": player.name,
        "seed": player.seed,
        "status": player.status.value,
        "wins": player.wins,
        "losses": player.losses,
        "eliminated": player.status is PlayerStatus.ELIMINATED,
        "withdrawn": player.status is PlayerStatus.WITHDRAWN,
    }


def order_players(players: Sequence[Player]) -> list[Player]:
    """Order players for display: fewest losses, most wins, then name."""

    return sorted(players, key=lambda player: (player.losses, -player.wins, player.name))


def _find_champion(
    players: Sequence[Player], system_status: SystemStatus
) -> Player | None:
    """Return the winner once the tournament is marked complete.

    A completed event leaves exactly one player who is neither eliminated nor
    withdrawn. Anything else (for example a deleted player) yields ``None`` so
    the frontend can fall back to the standings instead of guessing.
    """

    if system_status is not SystemStatus.COMPLETED:
        return None
    alive = [player for player in players if player.status in _ALIVE_STATES]
    return alive[0] if len(alive) == 1 else None


def serialize_match(
    match: Match,
    players_by_id: dict[str, Player],
    matches: Sequence[Match],
) -> dict[str, Any]:
    """Serialize one match with resolved player names.

    Args:
        match: Match to serialize.
        players_by_id: Lookup of every player of the tournament.
        matches: All matches of the tournament, needed by the dead-slot
            analysis.

    Returns:
        A JSON-safe dictionary consumed by the mini-app.
    """

    winner = players_by_id.get(match.winner_id) if match.winner_id else None
    return {
        "id": match.id,
        "bracket": match.bracket.value,
        "round": match.round,
        "position": match.position,
        "status": match.status.value,
        "player1": _player_ref(
            players_by_id.get(match.player1_id) if match.player1_id else None
        ),
        "player2": _player_ref(
            players_by_id.get(match.player2_id) if match.player2_id else None
        ),
        "winner": _player_ref(winner),
        "loser": _player_ref(
            players_by_id.get(match.loser_id) if match.loser_id else None
        ),
        "score": {
            "player1": match.score_player1,
            "player2": match.score_player2,
        },
        "is_bye": match.is_bye,
        "is_dead": is_match_dead(matches, match),
        "started_at": _isoformat(match.started_at),
        "completed_at": _isoformat(match.completed_at),
    }


async def build_system_list(storage: StorageManager) -> dict[str, Any]:
    """Return the tournament selector payload, newest tournament first."""

    systems = await storage.list_systems()
    players = await storage.list_players()
    matches = await storage.list_matches()

    player_counts = Counter(player.system_id for player in players)
    match_counts = Counter(match.system_id for match in matches)
    ready_counts = Counter(
        match.system_id
        for match in matches
        if match.status is MatchStatus.READY
    )

    items = []
    for system in sorted(systems, key=lambda item: item.created_at, reverse=True):
        items.append(
            {
                "id": system.id,
                "name": system.name,
                "description": system.description,
                "status": system.status.value,
                "registration_open": system.registration_open,
                "max_players": system.max_players,
                "player_count": player_counts.get(system.id, 0),
                "match_count": match_counts.get(system.id, 0),
                "ready_count": ready_counts.get(system.id, 0),
                "created_at": _isoformat(system.created_at),
                "updated_at": _isoformat(system.updated_at),
            }
        )

    return {"schema": API_SCHEMA_VERSION, "systems": items}


async def build_dashboard(
    storage: StorageManager, system_id: str
) -> dict[str, Any]:
    """Return everything the mini-app needs to render one tournament.

    Raises:
        core.storage.RecordNotFoundError: If ``system_id`` is unknown.
    """

    system = await storage.require_system(system_id)
    players = await storage.list_players(system.id)
    matches = await storage.list_matches(system.id)
    players_by_id = {player.id: player for player in players}

    ordered_matches = sorted(
        matches, key=lambda match: (match.round, match.position)
    )
    brackets: dict[str, list[dict[str, Any]]] = {
        side.value: [] for side in BRACKETS
    }
    for match in ordered_matches:
        brackets[match.bracket.value].append(
            serialize_match(match, players_by_id, matches)
        )

    champion = _find_champion(players, system.status)
    alive = [player for player in players if player.status in _ALIVE_STATES]

    return {
        "schema": API_SCHEMA_VERSION,
        "generated_at": _isoformat(datetime.now().astimezone()),
        "system": {
            "id": system.id,
            "name": system.name,
            "description": system.description,
            "status": system.status.value,
            "registration_open": system.registration_open,
            "max_players": system.max_players,
            "player_count": len(players),
            "created_at": _isoformat(system.created_at),
            "updated_at": _isoformat(system.updated_at),
        },
        "champion": _player_ref(champion),
        "players": [_player_summary(player) for player in order_players(players)],
        "brackets": brackets,
        "counts": {
            "players": len(players),
            "matches": len(matches),
            "completed": sum(
                1 for match in matches if match.status is MatchStatus.COMPLETED
            ),
            "ready": sum(
                1 for match in matches if match.status is MatchStatus.READY
            ),
            "active": sum(
                1 for match in matches if match.status is MatchStatus.ACTIVE
            ),
            "byes": sum(1 for match in matches if match.is_bye),
            "remaining": len(alive),
        },
    }
