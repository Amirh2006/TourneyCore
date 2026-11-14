"""aiogram router implementing the player-facing Telegram commands."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core.storage import StorageError, StorageManager

from bot.keyboards import webapp_keyboard
from bot.service import (
    format_bracket,
    format_next_match,
    format_profile,
    format_standings,
    load_player,
    next_match_for,
    register_by_code,
)

#: Welcome text reused by ``/start`` and ``/help``.
WELCOME_TEXT = (
    "👋 Welcome to TourneyCore!\n\n"
    "Commands:\n"
    "/register CODE — redeem the entry code from your admin\n"
    "/me — your profile and record\n"
    "/next — your next match\n"
    "/bracket — the full tournament bracket\n"
    "/standings — current standings\n"
    "/app — open the live bracket mini-app\n"
    "/help — show this message\n\n"
    "You can also send your 8-character entry code directly."
)


def build_router(storage: StorageManager, webapp_url: str | None = None) -> Router:
    """Create a router whose handlers use ``storage``.

    A fresh router is returned on every call so tests can build isolated
    dispatchers without sharing middleware or handler state.

    Args:
        storage: Storage manager backing every handler.
        webapp_url: Optional HTTPS address of the live mini-app. When it is
            set, ``/start`` and ``/app`` show a Web App button.
    """

    router = Router(name="tourneycore")

    def app_markup():
        """Return the mini-app keyboard, or ``None`` when unconfigured."""

        return webapp_keyboard(webapp_url) if webapp_url else None

    async def require_player(message: Message, telegram_id: int):
        """Return the sender's player record, or answer and return ``None``."""

        player = await load_player(storage, telegram_id)
        if player is None:
            await message.answer(
                "You are not registered yet. Send the entry code from your "
                "admin, for example: /register A1B2C3D4"
            )
            return None
        return player

    @router.message(Command("start"))
    async def handle_start(message: Message) -> None:
        player = await load_player(storage, message.from_user.id)
        text = WELCOME_TEXT
        if player is not None:
            text = f"Welcome back, {player.name}!\n\n{WELCOME_TEXT}"
        await message.answer(text, reply_markup=app_markup())

    @router.message(Command("help"))
    async def handle_help(message: Message) -> None:
        await message.answer(WELCOME_TEXT)

    @router.message(Command("app"))
    async def handle_app(message: Message) -> None:
        if not webapp_url:
            await message.answer(
                "The live bracket mini-app is not configured yet. Ask the "
                "organizer to set TOURNEYCORE_WEBAPP_URL."
            )
            return
        await message.answer(
            "📺 The bracket updates live while you watch.",
            reply_markup=webapp_keyboard(webapp_url),
        )

    async def do_register(message: Message, code: str) -> None:
        if not code:
            await message.answer("Usage: /register A1B2C3D4")
            return
        try:
            player = await register_by_code(
                storage,
                code,
                message.from_user.id,
                message.from_user.username,
            )
        except StorageError as error:
            await message.answer(f"Registration failed: {error}")
            return
        await message.answer(
            f"✅ Registered as {player.name}!\n"
            f"Your entry code {player.entry_code} is now linked to this account."
        )

    @router.message(Command("register"))
    async def handle_register(
        message: Message, command: CommandObject
    ) -> None:
        await do_register(message, (command.args or "").strip())

    @router.message(F.text.regexp(r"^\s*[A-Za-z0-9]{8}\s*$"))
    async def handle_bare_code(message: Message) -> None:
        await do_register(message, (message.text or "").strip())

    @router.message(Command("me"))
    async def handle_me(message: Message) -> None:
        player = await require_player(message, message.from_user.id)
        if player is None:
            return
        await message.answer(format_profile(player))

    @router.message(Command("next"))
    async def handle_next(message: Message) -> None:
        player = await require_player(message, message.from_user.id)
        if player is None:
            return
        players = await storage.list_players(player.system_id)
        matches = await storage.list_matches(player.system_id)
        upcoming = next_match_for(player, players, matches)
        await message.answer(format_next_match(upcoming, players, player_id=player.id))

    @router.message(Command("bracket"))
    async def handle_bracket(message: Message) -> None:
        player = await require_player(message, message.from_user.id)
        if player is None:
            return
        players = await storage.list_players(player.system_id)
        matches = await storage.list_matches(player.system_id)
        await message.answer(format_bracket(players, matches))

    @router.message(Command("standings"))
    async def handle_standings(message: Message) -> None:
        player = await require_player(message, message.from_user.id)
        if player is None:
            return
        players = await storage.list_players(player.system_id)
        await message.answer(format_standings(players))

    return router
