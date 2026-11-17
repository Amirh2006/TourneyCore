"""Entry point for the TourneyCore Telegram bot.

Start the bot with::

    set TOURNEYCORE_BOT_TOKEN=123456:ABC...
    python -m bot.main

The token is read from the environment so it never appears in shell history or
source control.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import MenuButtonWebApp, WebAppInfo

from core.storage import DEFAULT_DATA_DIR, StorageManager

from bot.config import DATA_DIR_ENV, BotSettings, ConfigError
from bot.handlers import build_router

LOGGER = logging.getLogger("tourneycore.bot")


async def run(settings: BotSettings) -> None:
    """Initialise storage, register the router and start long polling."""

    storage = StorageManager(data_dir=settings.data_dir)
    await storage.initialize()

    # Cloudflare proxy setup for Telegram connection (bypassing restrictions)
    custom_session = AiohttpSession(
        api=TelegramAPIServer.from_base("https://tg-proxy.amir-h-porsharifi.workers.dev")
    )

    bot = Bot(token=settings.token, session=custom_session)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(storage, webapp_url=settings.webapp_url)
    )

    if settings.webapp_url:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🏆 Live bracket",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            )
        except Exception:  # noqa: BLE001 - a menu button is a nice-to-have
            LOGGER.warning(
                "Could not install the mini-app menu button; /app still works.",
                exc_info=True,
            )

    LOGGER.info("Bot started; data directory is %s", settings.data_dir)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def build_parser() -> argparse.ArgumentParser:
    """Create the small launcher argument parser."""

    parser = argparse.ArgumentParser(prog="tourneycore-bot")
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "JSON data directory. Defaults to $"
            f"{DATA_DIR_ENV} or {DEFAULT_DATA_DIR}."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bot until interrupted."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = BotSettings.from_env()
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1

    if args.data_dir:
        settings = BotSettings(
            token=settings.token,
            data_dir=Path(args.data_dir),
            webapp_url=settings.webapp_url,
        )

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())