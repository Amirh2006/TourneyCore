"""Runtime configuration for the TourneyCore Telegram bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.storage import DEFAULT_DATA_DIR

#: Environment variable that holds the Telegram bot token.
BOT_TOKEN_ENV = "TOURNEYCORE_BOT_TOKEN"

#: Environment variable that overrides the JSON data directory.
DATA_DIR_ENV = "TOURNEYCORE_DATA_DIR"


class ConfigError(RuntimeError):
    """Raised when the bot cannot start because configuration is missing."""


@dataclass(frozen=True)
class BotSettings:
    """Settings required to run the polling bot."""

    token: str
    data_dir: Path

    @classmethod
    def from_env(cls) -> BotSettings:
        """Build settings from environment variables.

        Raises:
            ConfigError: If ``TOURNEYCORE_BOT_TOKEN`` is not set.
        """

        token = os.environ.get(BOT_TOKEN_ENV, "").strip()
        if not token:
            raise ConfigError(
                f"Set the {BOT_TOKEN_ENV} environment variable to the bot token."
            )

        raw_dir = os.environ.get(DATA_DIR_ENV, "").strip()
        data_dir = Path(raw_dir) if raw_dir else DEFAULT_DATA_DIR
        return cls(token=token, data_dir=data_dir)
