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

#: Environment variable with the public HTTPS URL of the mini-app.
WEBAPP_URL_ENV = "TOURNEYCORE_WEBAPP_URL"


class ConfigError(RuntimeError):
    """Raised when the bot cannot start because configuration is missing."""


@dataclass(frozen=True)
class BotSettings:
    """Settings required to run the polling bot."""

    token: str
    data_dir: Path
    #: Public HTTPS address of the mini-app, or ``None`` when not configured.
    webapp_url: str | None = None

    @classmethod
    def from_env(cls) -> BotSettings:
        """Build settings from environment variables.

        Raises:
            ConfigError: If ``TOURNEYCORE_BOT_TOKEN`` is missing, or if
                ``TOURNEYCORE_WEBAPP_URL`` is set but is not an HTTPS URL
                (Telegram refuses to open anything else).
        """

        token = os.environ.get(BOT_TOKEN_ENV, "").strip()
        if not token:
            raise ConfigError(
                f"Set the {BOT_TOKEN_ENV} environment variable to the bot token."
            )

        raw_dir = os.environ.get(DATA_DIR_ENV, "").strip()
        data_dir = Path(raw_dir) if raw_dir else DEFAULT_DATA_DIR

        webapp_url = os.environ.get(WEBAPP_URL_ENV, "").strip() or None
        if webapp_url is not None and not webapp_url.startswith("https://"):
            raise ConfigError(
                f"{WEBAPP_URL_ENV} must start with https:// because Telegram "
                "only opens secure mini-app URLs."
            )

        return cls(token=token, data_dir=data_dir, webapp_url=webapp_url)
