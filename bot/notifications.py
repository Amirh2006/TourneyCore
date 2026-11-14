"""Push notifications for match changes.

The functions here are the only place that talks to the Telegram Bot API for
outgoing messages. They accept a ready ``aiogram.Bot`` so tests can substitute a
fake object with a ``send_message`` coroutine.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from core.models import Match, MatchStatus, Player

from bot.service import BRACKET_LABELS, player_name


class MessageSender(Protocol):
    """Minimal interface shared by ``aiogram.Bot`` and test doubles."""

    async def send_message(self, chat_id: int, text: str) -> object: ...


def ready_match_text(
    match: Match, players: Sequence[Player], player_id: str
) -> str:
    """Build the notification body for one participant of a ready match."""

    opponent_id = (
        match.player2_id if match.player1_id == player_id else match.player1_id
    )
    opponent = player_name(players, opponent_id)
    side = BRACKET_LABELS.get(match.bracket, match.bracket.value)
    lines = [
        "🎮 Your next match is ready!",
        f"Opponent: {opponent}",
        f"Bracket: {side}, round {match.round}",
        f"Match id: {match.id}",
    ]
    if match.bracket is not None and match.status is MatchStatus.READY:
        lines.append("Good luck! Report to the admin when you finish.")
    return "\n".join(lines)


async def notify_ready_matches(
    sender: MessageSender,
    matches: Sequence[Match],
    players: Sequence[Player],
) -> int:
    """Notify every registered participant of a READY match.

    Returns the number of messages that were delivered. Delivery failures for a
    single player (blocked bot, deleted account) never abort the batch.
    """

    by_id = {player.id: player for player in players}
    sent = 0

    for match in matches:
        if match.status is not MatchStatus.READY or match.is_bye:
            continue
        for player_id in (match.player1_id, match.player2_id):
            player = by_id.get(player_id) if player_id else None
            if player is None or player.telegram_id is None:
                continue
            try:
                await sender.send_message(
                    player.telegram_id,
                    ready_match_text(match, players, player.id),
                )
                sent += 1
            except Exception:  # noqa: BLE001 - a blocked bot must not stop the batch
                continue

    return sent
