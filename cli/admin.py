"""Administrator command line interface for TourneyCore.

The CLI is the control panel used while an event is running. It talks to the
asynchronous JSON storage layer through :class:`~core.storage.StorageManager`
and to the tournament rules through :mod:`core.bracket`, so it never mutates a
JSON file directly.

Run it with::

    python -m cli.admin --help
    python -m cli.admin init "Friday Cup" --max-players 8
    python -m cli.admin add-player sys_123 Alice
    python -m cli.admin register A1B2C3D4 --telegram-id 123456789
    python -m cli.admin seed sys_123
    python -m cli.admin start m_123
    python -m cli.admin win m_123 plr_abc
    python -m cli.admin show-bracket sys_123

Every command prints a short human readable result and uses exit code ``1`` on
failure, which makes the tool scriptable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Sequence

from core.bracket import (
    advance_match,
    build_bracket,
    is_match_dead,
    is_tournament_over,
)
from core.models import (
    BracketSide,
    Match,
    MatchStatus,
    Player,
    PlayerStatus,
    SystemStatus,
    WinMethod,
)
from core.storage import DEFAULT_DATA_DIR, StorageError, StorageManager

#: Type of the coroutine each sub-command returns.
Command = Callable[[argparse.Namespace, StorageManager], Awaitable[int]]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _player_name(players: Sequence[Player], player_id: str | None) -> str:
    """Return a readable player name for ``player_id``."""

    if player_id is None:
        return "-"
    for player in players:
        if player.id == player_id:
            return player.name
    return player_id


def _match_line(match: Match, players: Sequence[Player]) -> str:
    """Format one match as a single aligned line."""

    left = _player_name(players, match.player1_id)
    right = _player_name(players, match.player2_id)
    if match.status is MatchStatus.COMPLETED and match.is_bye:
        winner = _player_name(players, match.winner_id)
        result = f"bye -> {winner}"
    elif match.status is MatchStatus.COMPLETED and match.winner_id is not None:
        winner = _player_name(players, match.winner_id)
        score = ""
        if match.score_player1 is not None or match.score_player2 is not None:
            score = f" ({match.score_player1}-{match.score_player2})"
        result = f"winner: {winner}{score}"
    else:
        result = match.status.value

    bracket = match.bracket.value.replace("_", " ")
    return (
        f"  {match.id:<18} {bracket:<12} R{match.round} "
        f"#{match.position}  {left} vs {right}  [{result}]"
    )


def _print_matches(matches: Sequence[Match], players: Sequence[Player]) -> None:
    """Print matches grouped by bracket side, hiding unreachable ones."""

    visible = [
        match
        for match in matches
        if not (match.status is MatchStatus.PENDING and is_match_dead(matches, match))
    ]

    groups = (
        (BracketSide.WINNERS, "Winners bracket"),
        (BracketSide.LOSERS, "Losers bracket"),
        (BracketSide.GRAND_FINAL, "Grand final"),
    )
    for side, title in groups:
        group = [match for match in visible if match.bracket is side]
        if not group:
            continue
        print(f"{title}:")
        for match in sorted(group, key=lambda item: (item.round, item.position)):
            print(_match_line(match, players))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_init(args: argparse.Namespace, storage: StorageManager) -> int:
    """Create a new gaming system and open registration."""

    system = await storage.create_system(
        args.name,
        description=args.description,
        max_players=args.max_players,
    )
    system.status = SystemStatus.REGISTRATION
    system.registration_open = True
    await storage.save_system(system)

    print(f"Created tournament {system.id} ({system.name}).")
    print(f"Registration is open for up to {system.max_players} players.")
    return 0


async def cmd_add_player(args: argparse.Namespace, storage: StorageManager) -> int:
    """Create a player and print the entry code the player must redeem."""

    system = await storage.require_system(args.system_id)
    if system.is_full:
        print(
            f"Tournament {system.id} is already full "
            f"({system.player_count}/{system.max_players}).",
            file=sys.stderr,
        )
        return 1

    player = await storage.create_player(system.id, args.name)
    system.add_player(player.id)
    await storage.save_system(system)

    print(f"Added {player.name} ({player.id}).")
    print(f"Entry code: {player.entry_code}")
    return 0


async def cmd_register(args: argparse.Namespace, storage: StorageManager) -> int:
    """Redeem an entry code and link a Telegram account."""

    player = await storage.redeem_entry_code(
        args.entry_code,
        args.telegram_id,
        args.username,
    )
    print(f"Player {player.name} ({player.id}) is now registered.")
    return 0


async def cmd_list_systems(args: argparse.Namespace, storage: StorageManager) -> int:
    """List every tournament."""

    systems = await storage.list_systems()
    if not systems:
        print("No tournaments yet. Create one with 'init'.")
        return 0

    print(f"{'ID':<20} {'STATUS':<14} {'PLAYERS':<9} NAME")
    for system in systems:
        count = f"{system.player_count}/{system.max_players}"
        print(f"{system.id:<20} {system.status.value:<14} {count:<9} {system.name}")
    return 0


async def cmd_list_players(args: argparse.Namespace, storage: StorageManager) -> int:
    """List the players of a tournament."""

    await storage.require_system(args.system_id)
    players = await storage.list_players(args.system_id)
    if not players:
        print("No players registered yet.")
        return 0

    print(f"{'ID':<20} {'STATUS':<12} {'CODE':<10} {'W-L':<7} NAME")
    for player in players:
        record = f"{player.wins}-{player.losses}"
        print(
            f"{player.id:<20} {player.status.value:<12} "
            f"{player.entry_code:<10} {record:<7} {player.name}"
        )
    return 0


async def cmd_list_matches(args: argparse.Namespace, storage: StorageManager) -> int:
    """List the bracket of a tournament."""

    await storage.require_system(args.system_id)
    players = await storage.list_players(args.system_id)
    matches = await storage.list_matches(args.system_id)
    if not matches:
        print("No bracket yet. Run 'seed' once registration is closed.")
        return 0

    _print_matches(matches, players)
    return 0


async def cmd_seed(args: argparse.Namespace, storage: StorageManager) -> int:
    """Generate the double-elimination bracket for a tournament."""

    system = await storage.require_system(args.system_id)
    players = await storage.list_players(system.id)
    by_id = {player.id: player for player in players}

    participants = [
        by_id[player_id]
        for player_id in system.player_ids
        if player_id in by_id
        and by_id[player_id].status
        in (PlayerStatus.REGISTERED, PlayerStatus.ACTIVE)
    ]

    if len(participants) < 2:
        print(
            "At least two registered players are required to seed the bracket.",
            file=sys.stderr,
        )
        return 1

    try:
        matches = build_bracket(system.id, [player.id for player in participants])
    except ValueError as error:
        print(f"Cannot seed bracket: {error}", file=sys.stderr)
        return 1

    # Preserve the brackets of other tournaments when replacing the document.
    other_matches = [
        match
        for match in await storage.list_matches()
        if match.system_id != system.id
    ]
    await storage.save_matches([*other_matches, *matches])

    for player in participants:
        player.wins = 0
        player.losses = 0
        player.eliminated_at = None
        player.mark_active()
        await storage.save_player(player)

    system.status = SystemStatus.RUNNING
    system.registration_open = False
    await storage.save_system(system)

    print(f"Seeded {len(participants)} players into {len(matches)} matches.")
    print("First round is READY; use 'list-matches' to see the bracket.")
    return 0


async def cmd_start(args: argparse.Namespace, storage: StorageManager) -> int:
    """Start a READY match."""

    match = await storage.require_match(args.match_id)
    try:
        match.start()
    except ValueError as error:
        print(f"Cannot start match: {error}", file=sys.stderr)
        return 1

    await storage.save_match(match)
    print(f"Match {match.id} is now in progress.")
    return 0


async def _record_result(
    storage: StorageManager,
    match_id: str,
    winner_id: str,
    method: WinMethod,
    score_player1: int | None,
    score_player2: int | None,
    notify: bool = False,
) -> int:
    """Shared implementation of ``win`` and ``walkover``."""

    match = await storage.require_match(match_id)
    players = await storage.list_players(match.system_id)
    matches = await storage.list_matches(match.system_id)

    ready_before = {
        item.id for item in matches if item.status is MatchStatus.READY
    }

    # ``advance_match`` mutates the instance stored in ``matches``. Resolve it
    # here so the version written back to disk is the updated one, not the
    # separately loaded copy that ``require_match`` returned.
    target = next((item for item in matches if item.id == match_id), None)
    if target is None:
        print(f"Match {match_id!r} not found for system {match.system_id}.", file=sys.stderr)
        return 1

    try:
        next_winner, next_loser = advance_match(
            matches,
            target.id,
            winner_id,
            method=method,
            score_player1=score_player1,
            score_player2=score_player2,
            players=players,
        )
    except ValueError as error:
        print(f"Cannot record result: {error}", file=sys.stderr)
        return 1

    # Persist the whole bracket of this system. A bye cascade can complete
    # intermediate matches that are not returned by ``advance_match``, so
    # saving only the direct targets would drop those updates.
    other_systems = [
        item
        for item in await storage.list_matches()
        if item.system_id != match.system_id
    ]
    await storage.save_matches([*other_systems, *matches])

    for player in players:
        await storage.save_player(player)

    winner = next((p for p in players if p.id == winner_id), None)
    winner_name = winner.name if winner else winner_id
    print(f"{winner_name} wins match {match.id} ({method.value}).")

    system = await storage.get_system(match.system_id)
    if system is not None and is_tournament_over(matches, players):
        system.status = SystemStatus.COMPLETED
        await storage.save_system(system)
        print(f"Tournament {system.id} is complete.")

    if next_winner is not None and next_winner is next_loser:
        print(f"Next match: {next_winner.id} [{next_winner.status.value}]")
    else:
        for label, target in (
            ("Next winners match", next_winner),
            ("Next losers match", next_loser),
        ):
            if target is not None:
                print(f"{label}: {target.id} [{target.status.value}]")

    if notify:
        newly_ready = [
            item
            for item in matches
            if item.status is MatchStatus.READY and item.id not in ready_before
        ]
        await _notify_players(newly_ready, players)

    return 0


async def _notify_players(matches: Sequence[Match], players: Sequence[Player]) -> None:
    """Send Telegram notifications for matches that just became ready."""

    if not matches:
        return

    token = os.environ.get("TOURNEYCORE_BOT_TOKEN", "").strip()
    if not token:
        print(
            "Warning: --notify was given but TOURNEYCORE_BOT_TOKEN is not set.",
            file=sys.stderr,
        )
        return

    # Imported lazily so the CLI works for admins who never use the bot.
    from aiogram import Bot

    from bot.notifications import notify_ready_matches

    bot = Bot(token)
    try:
        sent = await notify_ready_matches(bot, matches, players)
    finally:
        await bot.session.close()
    print(f"Sent {sent} Telegram notification(s).")


async def cmd_win(args: argparse.Namespace, storage: StorageManager) -> int:
    """Record a played match."""

    return await _record_result(
        storage,
        args.match_id,
        args.player_id,
        WinMethod.NORMAL,
        args.score1,
        args.score2,
        notify=args.notify,
    )


async def cmd_walkover(args: argparse.Namespace, storage: StorageManager) -> int:
    """Record a walkover."""

    return await _record_result(
        storage,
        args.match_id,
        args.player_id,
        WinMethod.WALKOVER,
        None,
        None,
        notify=args.notify,
    )


async def cmd_show_bracket(
    args: argparse.Namespace, storage: StorageManager
) -> int:
    """Print a compact view of the bracket."""

    return await cmd_list_matches(args, storage)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser with every sub-command."""

    parser = argparse.ArgumentParser(
        prog="tourney",
        description="TourneyCore administrator CLI.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory that holds systems.json, users.json and matches.json.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a tournament.")
    init_parser.add_argument("name", help="Tournament name.")
    init_parser.add_argument("--description", default="", help="Short description.")
    init_parser.add_argument(
        "--max-players", type=int, default=16, help="Maximum roster size."
    )
    init_parser.set_defaults(handler=cmd_init)

    add_parser = subparsers.add_parser(
        "add-player", help="Add a player and print their entry code."
    )
    add_parser.add_argument("system_id", help="Tournament identifier.")
    add_parser.add_argument("name", help="Player display name.")
    add_parser.set_defaults(handler=cmd_add_player)

    register_parser = subparsers.add_parser(
        "register", help="Redeem an entry code for a Telegram account."
    )
    register_parser.add_argument("entry_code", help="Code printed by add-player.")
    register_parser.add_argument("--telegram-id", type=int, required=True)
    register_parser.add_argument("--username", default=None)
    register_parser.set_defaults(handler=cmd_register)

    list_systems_parser = subparsers.add_parser(
        "list-systems", help="List every tournament."
    )
    list_systems_parser.set_defaults(handler=cmd_list_systems)

    list_players_parser = subparsers.add_parser(
        "list-players", help="List players of a tournament."
    )
    list_players_parser.add_argument("system_id")
    list_players_parser.set_defaults(handler=cmd_list_players)

    list_matches_parser = subparsers.add_parser(
        "list-matches", help="List the bracket of a tournament."
    )
    list_matches_parser.add_argument("system_id")
    list_matches_parser.set_defaults(handler=cmd_list_matches)

    show_parser = subparsers.add_parser(
        "show-bracket", help="Alias for list-matches."
    )
    show_parser.add_argument("system_id")
    show_parser.set_defaults(handler=cmd_show_bracket)

    seed_parser = subparsers.add_parser(
        "seed", help="Generate the double-elimination bracket."
    )
    seed_parser.add_argument("system_id")
    seed_parser.set_defaults(handler=cmd_seed)

    start_parser = subparsers.add_parser("start", help="Start a READY match.")
    start_parser.add_argument("match_id")
    start_parser.set_defaults(handler=cmd_start)

    win_parser = subparsers.add_parser("win", help="Record a normal result.")
    win_parser.add_argument("match_id")
    win_parser.add_argument("player_id")
    win_parser.add_argument("--score1", type=int, default=None)
    win_parser.add_argument("--score2", type=int, default=None)
    win_parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram messages to players whose next match became ready.",
    )
    win_parser.set_defaults(handler=cmd_win)

    walkover_parser = subparsers.add_parser("walkover", help="Record a walkover.")
    walkover_parser.add_argument("match_id")
    walkover_parser.add_argument("player_id")
    walkover_parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram messages to players whose next match became ready.",
    )
    walkover_parser.set_defaults(handler=cmd_walkover)

    return parser


async def _run(args: argparse.Namespace) -> int:
    """Open storage and dispatch to the selected command."""

    storage = StorageManager(data_dir=args.data_dir)
    await storage.initialize()
    handler: Command = args.handler
    return await handler(args, storage)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m cli.admin``."""

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_run(args))
    except StorageError as error:
        print(f"Storage error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
