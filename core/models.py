"""Pydantic data models for the TourneyCore tournament management system.

This module defines the core domain entities that are persisted in the local
file-based JSON database:

* :class:`GamingSystem` - a tournament (a game title) players register for.
* :class:`Player` - a participant that links a Telegram account to an
  administrator-generated entry code.
* :class:`Match` - a single double-elimination bout inside a bracket.

The models are pure, validated data containers. They never perform I/O:
persistence lives in :mod:`core.storage` and bracket progression lives in
:mod:`core.bracket`. Keeping the entities side-effect free makes them trivial
to serialize, test and reuse from the bot, the CLI and the frontend exporter.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# Characters that are easy to read out loud and hard to confuse (no O/0/I/1).
ENTRY_CODE_ALPHABET: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Default length of a generated entry code.
DEFAULT_ENTRY_CODE_LENGTH: int = 8


class PlayerStatus(str, Enum):
    """Lifecycle state of a registered player."""

    # An entry code was generated but the player has not redeemed it yet.
    PENDING = "pending"
    # The player redeemed their code and linked a Telegram account.
    REGISTERED = "registered"
    # The player is currently taking part in a running tournament.
    ACTIVE = "active"
    # The player collected two losses and is out of the tournament.
    ELIMINATED = "eliminated"
    # The player was removed from the tournament by an administrator.
    WITHDRAWN = "withdrawn"


class MatchStatus(str, Enum):
    """Lifecycle state of a single bracket match."""

    # At least one player slot is still empty.
    PENDING = "pending"
    # Both players are known and the match can be started.
    READY = "ready"
    # The match was started by an administrator and is in progress.
    ACTIVE = "active"
    # A winner and a loser have been recorded.
    COMPLETED = "completed"


class BracketSide(str, Enum):
    """Which bracket a match belongs to in a double-elimination event."""

    WINNERS = "winners"
    LOSERS = "losers"
    GRAND_FINAL = "grand_final"


class WinMethod(str, Enum):
    """How a match result was decided."""

    # A regular played match.
    NORMAL = "normal"
    # One player did not show up or conceded before playing.
    WALKOVER = "walkover"


class SystemStatus(str, Enum):
    """Lifecycle state of a gaming system (tournament)."""

    DRAFT = "draft"
    REGISTRATION = "registration"
    RUNNING = "running"
    COMPLETED = "completed"


class CoreModel(BaseModel):
    """Shared configuration for every persisted TourneyCore model.

    The configuration enforces a small set of project-wide invariants:

    * ``extra="forbid"`` - unknown JSON keys are rejected instead of silently
      dropped, which protects the database from schema drift.
    * ``validate_assignment=True`` - mutating a field re-runs validation, so an
      entity can never enter an invalid state during bracket progression.
    * ``str_strip_whitespace=True`` - user supplied names and descriptions are
      normalized before validation.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    @staticmethod
    def utc_now() -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""

        return datetime.now(timezone.utc)


def generate_model_id(prefix: str) -> str:
    """Build a short, human friendly, collision-resistant identifier.

    Args:
        prefix: Short domain prefix such as ``"sys"``, ``"plr"`` or ``"m"``.

    Returns:
        An identifier of the form ``"<prefix>_<12 hex chars>"``.
    """

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def generate_entry_code(length: int = DEFAULT_ENTRY_CODE_LENGTH) -> str:
    """Generate a random entry code used for Scenario A registration.

    Administrators hand these codes to players out of band; the player redeems
    the code on the Telegram bot, which pairs the code with a ``telegram_id``.

    Args:
        length: Number of characters in the code. Must be between 6 and 10 to
            match the :class:`Player` entry code constraints.

    Returns:
        An uppercase code drawn from an unambiguous alphabet.

    Raises:
        ValueError: If ``length`` is outside the supported range.
    """

    if not 6 <= length <= 10:
        raise ValueError("Entry code length must be between 6 and 10 characters.")

    return "".join(secrets.choice(ENTRY_CODE_ALPHABET) for _ in range(length))
