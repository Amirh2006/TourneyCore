"""Core domain layer for the TourneyCore tournament management system.

This package contains the framework-independent business logic of the project:

* :mod:`core.models` - Pydantic data models persisted in the JSON database.
* :mod:`core.storage` - Asynchronous, atomic JSON persistence layer.
* :mod:`core.bracket` - Double-elimination progression engine.

Only the core layer knows about tournament rules. The Telegram bot, the CLI
admin tools and the web frontend consume this package through its public API
and never mutate persisted state directly.
"""

from core.bracket import (
    advance_match,
    build_bracket,
    find_next_match,
    is_match_dead,
    is_tournament_over,
    make_grand_final,
)
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
from core.storage import (
    DEFAULT_DATA_DIR,
    CorruptDatabaseError,
    DatabaseValidationError,
    DuplicateEntryCodeError,
    JsonCollection,
    RecordNotFoundError,
    StorageError,
    StorageManager,
)

__all__ = [
    "BracketSide",
    "CorruptDatabaseError",
    "DEFAULT_DATA_DIR",
    "DatabaseValidationError",
    "DuplicateEntryCodeError",
    "GamingSystem",
    "JsonCollection",
    "Match",
    "MatchStatus",
    "Player",
    "PlayerStatus",
    "RecordNotFoundError",
    "StorageError",
    "StorageManager",
    "SystemStatus",
    "WinMethod",
    "advance_match",
    "build_bracket",
    "find_next_match",
    "generate_entry_code",
    "is_match_dead",
    "is_tournament_over",
    "make_grand_final",
]
