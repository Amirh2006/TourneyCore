"""Core domain layer for the TourneyCore tournament management system.

This package contains the framework-independent business logic of the project:

* :mod:`core.models` - Pydantic data models persisted in the JSON database.
* :mod:`core.storage` - Asynchronous JSON serialization layer (later phases).
* :mod:`core.bracket` - Double-elimination progression engine (later phases).

Only the core layer knows about tournament rules. The Telegram bot, the CLI
admin tools and the web frontend consume this package through its public API
and never mutate persisted state directly.
"""

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
    "BracketSide",
    "GamingSystem",
    "Match",
    "MatchStatus",
    "Player",
    "PlayerStatus",
    "SystemStatus",
    "WinMethod",
    "generate_entry_code",
]
