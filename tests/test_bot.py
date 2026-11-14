"""Tests for the Telegram bot service layer, formatting and notifications."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiogram import Router

from bot.config import (
    BOT_TOKEN_ENV,
    DATA_DIR_ENV,
    WEBAPP_URL_ENV,
    BotSettings,
    ConfigError,
)
from bot.handlers import WELCOME_TEXT, build_router
from bot.keyboards import webapp_keyboard
from bot.notifications import notify_ready_matches, ready_match_text
from bot.service import (
    format_bracket,
    format_next_match,
    format_profile,
    format_standings,
    load_player,
    next_match_for,
    register_by_code,
)
from core.bracket import build_bracket
from core.models import (
    BracketSide,
    MatchStatus,
    Player,
    PlayerStatus,
    generate_entry_code,
)
from core.storage import StorageManager


def make_player(name: str) -> Player:
    return Player(
        system_id="sys_1", name=name, entry_code=generate_entry_code()
    )


class FakeSender:
    """Records outgoing messages instead of calling Telegram."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.players = [make_player(name) for name in "ABCD"]
        self.matches = build_bracket("sys_1", [p.id for p in self.players])

    def test_bracket_text_contains_all_sections(self) -> None:
        text = format_bracket(self.players, self.matches)
        self.assertIn("Winners bracket", text)
        self.assertIn("Losers bracket", text)
        self.assertIn("Grand final", text)

    def test_bracket_text_when_unseeded(self) -> None:
        self.assertEqual(
            format_bracket(self.players, []), "The bracket has not been seeded yet."
        )

    def test_next_match_text_names_the_opponent(self) -> None:
        match = next(
            item
            for item in self.matches
            if item.bracket is BracketSide.WINNERS and item.round == 1
        )
        player_id = match.player1_id
        text = format_next_match(match, self.players, player_id=player_id)
        self.assertIn("Your next match", text)
        opponent = next(
            player for player in self.players if player.id == match.player2_id
        )
        self.assertIn(opponent.name, text)

    def test_next_match_absent(self) -> None:
        player = self.players[0]
        self.assertEqual(
            format_next_match(None, self.players, player_id=player.id),
            "You have no upcoming match right now.",
        )

    def test_profile_and_standings(self) -> None:
        profile = format_profile(self.players[0])
        self.assertIn(self.players[0].name, profile)
        self.assertIn("Record: 0W - 0L", profile)

        standings = format_standings(self.players)
        self.assertIn("Standings:", standings)

    def test_next_match_helper(self) -> None:
        player = self.players[0]
        found = next_match_for(player, self.players, self.matches)
        self.assertIsNotNone(found)
        self.assertIn(player.id, (found.player1_id, found.player2_id))


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_both_registered_players(self) -> None:
        first = make_player("Alice")
        second = make_player("Bob")
        first.link_telegram(111, "alice")
        second.link_telegram(222, "bob")

        match = next(
            item
            for item in build_bracket("sys_1", [first.id, second.id])
            if item.bracket is BracketSide.WINNERS
        )
        sender = FakeSender()

        sent = await notify_ready_matches(sender, [match], [first, second])

        self.assertEqual(sent, 2)
        chat_ids = {chat_id for chat_id, _ in sender.messages}
        self.assertEqual(chat_ids, {111, 222})

    async def test_skips_pending_and_unlinked_players(self) -> None:
        first = make_player("Alice")
        second = make_player("Bob")
        first.link_telegram(111)

        match = next(
            item
            for item in build_bracket("sys_1", [first.id, second.id])
            if item.bracket is BracketSide.WINNERS
        )
        # Drop the opponent: the match is no longer READY and must be skipped.
        match.assign_players(first.id, None)
        self.assertIs(match.status, MatchStatus.PENDING)
        sender = FakeSender()

        sent = await notify_ready_matches(sender, [match], [first, second])

        self.assertEqual(sent, 0)
        self.assertEqual(sender.messages, [])

    async def test_ready_message_mentions_opponent(self) -> None:
        first = make_player("Alice")
        second = make_player("Bob")
        match = next(
            item
            for item in build_bracket("sys_1", [first.id, second.id])
            if item.bracket is BracketSide.WINNERS
        )
        text = ready_match_text(match, [first, second], first.id)
        self.assertIn("Bob", text)
        self.assertIn(match.id, text)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = StorageManager(data_dir=Path(self._tmp.name))
        await self.storage.initialize()
        self.system = await self.storage.create_system("Cup", max_players=4)
        self.player = await self.storage.create_player(
            self.system.id, "Alice"
        )

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_register_by_code_links_account(self) -> None:
        registered = await register_by_code(
            self.storage, self.player.entry_code.lower(), 4242, "alice"
        )
        self.assertEqual(registered.telegram_id, 4242)
        self.assertIs(registered.status, PlayerStatus.REGISTERED)

    async def test_load_player_after_registration(self) -> None:
        self.assertIsNone(await load_player(self.storage, 4242))
        await register_by_code(self.storage, self.player.entry_code, 4242)
        found = await load_player(self.storage, 4242)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.player.id)


class HandlerWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_router_returns_router(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageManager(data_dir=Path(tmp))
            await storage.initialize()
            router = build_router(storage)
            self.assertIsInstance(router, Router)
            self.assertEqual(router.name, "tourneycore")

    async def test_build_router_accepts_a_webapp_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageManager(data_dir=Path(tmp))
            await storage.initialize()
            router = build_router(storage, webapp_url="https://example.com/")
            self.assertIsInstance(router, Router)

    def test_webapp_keyboard_opens_the_url(self) -> None:
        markup = webapp_keyboard("https://example.com/live")

        self.assertEqual(len(markup.inline_keyboard), 1)
        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.web_app.url, "https://example.com/live")
        self.assertIn("bracket", button.text.lower())

    def test_welcome_text_lists_the_app_command(self) -> None:
        self.assertIn("/app", WELCOME_TEXT)

    def test_welcome_text_lists_registration(self) -> None:
        self.assertIn("/register", WELCOME_TEXT)


class ConfigTests(unittest.TestCase):
    def test_missing_token_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                BotSettings.from_env()

    def test_reads_token_and_data_dir(self) -> None:
        env = {BOT_TOKEN_ENV: "token-123", DATA_DIR_ENV: r"C:\data"}
        with patch.dict(os.environ, env, clear=True):
            settings = BotSettings.from_env()
        self.assertEqual(settings.token, "token-123")
        self.assertEqual(str(settings.data_dir), r"C:\data")
        self.assertIsNone(settings.webapp_url)

    def test_reads_the_mini_app_url(self) -> None:
        env = {
            BOT_TOKEN_ENV: "token-123",
            WEBAPP_URL_ENV: "https://tourney.example.com/",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = BotSettings.from_env()
        self.assertEqual(settings.webapp_url, "https://tourney.example.com/")

    def test_rejects_an_insecure_mini_app_url(self) -> None:
        env = {
            BOT_TOKEN_ENV: "token-123",
            WEBAPP_URL_ENV: "http://tourney.example.com/",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                BotSettings.from_env()


if __name__ == "__main__":
    unittest.main()
