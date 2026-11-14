"""Integration tests for the admin CLI."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cli.admin import main


class CliFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            return main(["--data-dir", str(self.data_dir), *args])

    def read_json(self, name: str):
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    def seed_four_players(self) -> str:
        self.assertEqual(self.run_cli("init", "Test Cup", "--max-players", "4"), 0)
        system = self.read_json("systems.json")[0]
        system_id = system["id"]

        for name in ("Alice", "Bob", "Carol", "Dave"):
            self.assertEqual(self.run_cli("add-player", system_id, name), 0)

        players = self.read_json("users.json")
        for index, player in enumerate(players):
            self.assertEqual(
                self.run_cli(
                    "register",
                    player["entry_code"],
                    "--telegram-id",
                    str(1000 + index),
                ),
                0,
            )

        self.assertEqual(self.run_cli("seed", system_id), 0)
        return system_id

    def play_until_done(self, *, reset: bool) -> None:
        for _ in range(20):
            matches = self.read_json("matches.json")
            ready = sorted(
                (match for match in matches if match["status"] == "ready"),
                key=lambda item: (item["bracket"], item["round"], item["position"]),
            )
            if not ready:
                return
            match = ready[0]
            pick = match["player1_id"]
            if reset and match["bracket"] == "grand_final" and match["round"] == 1:
                pick = match["player2_id"]
            self.assertEqual(self.run_cli("start", match["id"]), 0)
            self.assertEqual(self.run_cli("win", match["id"], pick), 0)
        self.fail("tournament did not finish")

    def test_full_tournament_persists_results(self) -> None:
        system_id = self.seed_four_players()

        # First round must be READY and correctly seeded.
        matches = self.read_json("matches.json")
        first_round = [
            match
            for match in matches
            if match["bracket"] == "winners" and match["round"] == 1
        ]
        self.assertEqual(len(first_round), 2)
        self.assertTrue(all(match["status"] == "ready" for match in first_round))

        self.play_until_done(reset=False)

        matches = self.read_json("matches.json")
        completed = [match for match in matches if match["status"] == "completed"]
        self.assertEqual(len(completed), 6)  # undefeated champion, no reset

        # Bracket progression happened in memory *and* on disk.
        for match in completed:
            self.assertIsNotNone(match["winner_id"])
            self.assertIsNotNone(match["win_method"])

        system = self.read_json("systems.json")[0]
        self.assertEqual(system["status"], "completed")
        self.assertEqual(system["id"], system_id)

    def test_reset_final_is_played(self) -> None:
        self.seed_four_players()
        self.play_until_done(reset=True)

        matches = self.read_json("matches.json")
        grand_finals = [
            match for match in matches if match["bracket"] == "grand_final"
        ]
        self.assertEqual(len(grand_finals), 2)
        self.assertTrue(all(match["status"] == "completed" for match in grand_finals))

        system = self.read_json("systems.json")[0]
        self.assertEqual(system["status"], "completed")

    def test_seed_odd_roster_uses_a_bye(self) -> None:
        self.assertEqual(self.run_cli("init", "Odd Cup", "--max-players", "5"), 0)
        system_id = self.read_json("systems.json")[0]["id"]
        for name in ("A", "B", "C"):
            self.assertEqual(self.run_cli("add-player", system_id, name), 0)
        for index, player in enumerate(self.read_json("users.json")):
            self.assertEqual(
                self.run_cli(
                    "register", player["entry_code"], "--telegram-id", str(500 + index)
                ),
                0,
            )
        self.assertEqual(self.run_cli("seed", system_id), 0)

        matches = self.read_json("matches.json")
        byes = [match for match in matches if match.get("is_bye")]
        self.assertEqual(len(byes), 1)
        self.assertEqual(byes[0]["status"], "completed")

    def test_odd_tournament_can_finish(self) -> None:
        self.assertEqual(self.run_cli("init", "Three Cup", "--max-players", "3"), 0)
        system_id = self.read_json("systems.json")[0]["id"]
        for name in ("Alice", "Bob", "Carol"):
            self.assertEqual(self.run_cli("add-player", system_id, name), 0)
        for index, player in enumerate(self.read_json("users.json")):
            self.assertEqual(
                self.run_cli(
                    "register", player["entry_code"], "--telegram-id", str(700 + index)
                ),
                0,
            )
        self.assertEqual(self.run_cli("seed", system_id), 0)
        self.play_until_done(reset=False)
        self.assertEqual(self.read_json("systems.json")[0]["status"], "completed")

    def test_win_with_outsider_is_rejected(self) -> None:
        self.seed_four_players()
        matches = self.read_json("matches.json")
        ready = next(match for match in matches if match["status"] == "ready")
        outsider = next(
            player
            for player in self.read_json("users.json")
            if player["id"] not in (ready["player1_id"], ready["player2_id"])
        )
        self.assertEqual(self.run_cli("win", ready["id"], outsider["id"]), 1)


if __name__ == "__main__":
    unittest.main()
