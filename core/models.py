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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class GamingSystem(CoreModel):
    """A tournament for a single game title.

    A gaming system groups players and matches together. Administrators create
    one system per event (for example ``"Tekken 8 - Friday Cup"``), open
    registration, seed the bracket and then drive matches through the CLI.
    """

    id: str = Field(
        default_factory=lambda: generate_model_id("sys"),
        description="Stable identifier of the gaming system.",
    )
    name: str = Field(
        min_length=1,
        max_length=80,
        description="Display name of the game or event.",
    )
    description: str = Field(
        default="",
        max_length=280,
        description="Optional short description shown in the mini-app.",
    )
    max_players: int = Field(
        default=16,
        ge=2,
        le=512,
        description="Maximum number of players that may register.",
    )
    status: SystemStatus = Field(
        default=SystemStatus.DRAFT,
        description="Current lifecycle state of the tournament.",
    )
    registration_open: bool = Field(
        default=False,
        description="Whether players may still redeem entry codes.",
    )
    player_ids: list[str] = Field(
        default_factory=list,
        description="Ordered list of player identifiers; the order is the seed.",
    )
    created_at: datetime = Field(
        default_factory=CoreModel.utc_now,
        description="Creation timestamp in UTC.",
    )
    updated_at: datetime = Field(
        default_factory=CoreModel.utc_now,
        description="Last modification timestamp in UTC.",
    )

    @field_validator("name")
    @classmethod
    def _reject_blank_name(cls, value: str) -> str:
        """Reject a name made only of whitespace."""

        if not value.strip():
            raise ValueError("Name must not be blank.")
        return value

    @field_validator("player_ids")
    @classmethod
    def _reject_duplicate_players(cls, value: list[str]) -> list[str]:
        """Reject a roster that lists the same player twice."""

        if len(value) != len(set(value)):
            raise ValueError("player_ids must not contain duplicates.")
        return value

    @model_validator(mode="after")
    def _enforce_capacity(self) -> GamingSystem:
        """Keep the roster within the configured capacity."""

        if len(self.player_ids) > self.max_players:
            raise ValueError(
                f"player_ids contains {len(self.player_ids)} entries but "
                f"max_players is {self.max_players}."
            )
        return self

    @property
    def player_count(self) -> int:
        """Return the number of players currently in the roster."""

        return len(self.player_ids)

    @property
    def is_full(self) -> bool:
        """Return ``True`` when no further player may register."""

        return self.player_count >= self.max_players

    @property
    def can_start(self) -> bool:
        """Return ``True`` when a double-elimination bracket is possible."""

        return self.player_count >= 2 and self.status is SystemStatus.REGISTRATION

    def add_player(self, player_id: str) -> None:
        """Append a player to the roster while respecting capacity.

        Args:
            player_id: Identifier of the player to enroll.

        Raises:
            ValueError: If the roster is full or the player is already listed.
        """

        if self.is_full:
            raise ValueError("Cannot add player: the gaming system is full.")
        if player_id in self.player_ids:
            raise ValueError(f"Player {player_id!r} is already enrolled.")
        self.player_ids.append(player_id)
        self.updated_at = CoreModel.utc_now()

    def remove_player(self, player_id: str) -> None:
        """Remove a player from the roster, ignoring unknown identifiers."""

        if player_id in self.player_ids:
            self.player_ids.remove(player_id)
            self.updated_at = CoreModel.utc_now()


class Player(CoreModel):
    """A tournament participant linked to a Telegram account.

    Registration follows Scenario A: an administrator generates a unique entry
    code, hands it to the player out of band, and the player redeems it on the
    Telegram bot. Redeeming the code pairs the bot account (``telegram_id``)
    with the pre-created player record, so the bracket can notify them directly.
    """

    id: str = Field(
        default_factory=lambda: generate_model_id("plr"),
        description="Stable identifier of the player.",
    )
    system_id: str = Field(
        min_length=1,
        description="Identifier of the gaming system the player belongs to.",
    )
    name: str = Field(
        min_length=1,
        max_length=64,
        description="In-game display name of the player.",
    )
    entry_code: str = Field(
        min_length=6,
        max_length=10,
        description="Administrator-generated registration code.",
    )
    telegram_id: int | None = Field(
        default=None,
        gt=0,
        description="Telegram user id once the entry code was redeemed.",
    )
    telegram_username: str | None = Field(
        default=None,
        max_length=64,
        description="Optional Telegram username without the leading '@'.",
    )
    status: PlayerStatus = Field(
        default=PlayerStatus.PENDING,
        description="Current lifecycle state of the player.",
    )
    seed: int | None = Field(
        default=None,
        ge=1,
        description="Bracket seed assigned when the tournament starts.",
    )
    wins: int = Field(default=0, ge=0, description="Number of matches won.")
    losses: int = Field(default=0, ge=0, description="Number of matches lost.")
    created_at: datetime = Field(
        default_factory=CoreModel.utc_now,
        description="Creation timestamp in UTC.",
    )
    registered_at: datetime | None = Field(
        default=None,
        description="Timestamp of a successful entry code redemption.",
    )
    eliminated_at: datetime | None = Field(
        default=None,
        description="Timestamp of the second (eliminating) loss.",
    )

    @field_validator("name")
    @classmethod
    def _reject_blank_name(cls, value: str) -> str:
        """Reject a name made only of whitespace."""

        if not value.strip():
            raise ValueError("Name must not be blank.")
        return value

    @field_validator("entry_code")
    @classmethod
    def _normalize_entry_code(cls, value: str) -> str:
        """Normalize an entry code to its canonical uppercase form."""

        normalized = value.strip().upper().replace("-", "").replace(" ", "")
        if not normalized.isalnum():
            raise ValueError("Entry code must contain letters and digits only.")
        return normalized

    @field_validator("telegram_username")
    @classmethod
    def _normalize_username(cls, value: str | None) -> str | None:
        """Strip the optional leading '@' from a Telegram username."""

        if value is None:
            return None
        return value.lstrip("@")

    @model_validator(mode="after")
    def _enforce_lifecycle_consistency(self) -> Player:
        """Ensure status flags match the registration state."""

        if self.status in (PlayerStatus.REGISTERED, PlayerStatus.ACTIVE):
            if self.telegram_id is None:
                raise ValueError(
                    f"A player in status {self.status.value!r} must have a "
                    "telegram_id."
                )
            if self.registered_at is None:
                raise ValueError(
                    f"A player in status {self.status.value!r} must have a "
                    "registered_at timestamp."
                )
        if self.status is PlayerStatus.ELIMINATED and self.eliminated_at is None:
            raise ValueError("An eliminated player must have an eliminated_at timestamp.")
        return self

    @property
    def is_registered(self) -> bool:
        """Return ``True`` when the player redeemed their entry code."""

        return self.telegram_id is not None

    @property
    def is_eliminated(self) -> bool:
        """Return ``True`` when the player is out of the tournament."""

        return self.status is PlayerStatus.ELIMINATED

    @property
    def can_play(self) -> bool:
        """Return ``True`` when the player may be scheduled in a match."""

        return self.status in (PlayerStatus.REGISTERED, PlayerStatus.ACTIVE)

    def link_telegram(self, telegram_id: int, username: str | None = None) -> None:
        """Pair a Telegram account with this player by redeeming the code.

        Args:
            telegram_id: Telegram user id of the player.
            username: Optional Telegram username without the leading '@'.

        Raises:
            ValueError: If the player already redeemed an entry code.
        """

        if self.is_registered:
            raise ValueError("This player already linked a Telegram account.")

        self.telegram_id = telegram_id
        self.telegram_username = username.lstrip("@") if username else None
        self.registered_at = CoreModel.utc_now()
        self.status = PlayerStatus.REGISTERED

    def mark_active(self) -> None:
        """Move a registered player into the active tournament state."""

        if not self.is_registered:
            raise ValueError("Cannot activate a player without a telegram_id.")
        self.status = PlayerStatus.ACTIVE

    def register_win(self) -> None:
        """Record a match win for this player."""

        self.wins += 1

    def register_loss(self) -> bool:
        """Record a match loss and eliminate the player on the second one.

        Returns:
            ``True`` when this loss eliminated the player from the tournament.
        """

        self.losses += 1
        if self.losses >= 2 and self.status is not PlayerStatus.ELIMINATED:
            self.eliminated_at = CoreModel.utc_now()
            self.status = PlayerStatus.ELIMINATED
            return True
        return False

    def withdraw(self) -> None:
        """Mark the player as removed by an administrator."""

        self.status = PlayerStatus.WITHDRAWN
