"""Tests for the read-only web API and the mini-app HTTP server."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from core.bracket import advance_match, build_bracket, is_tournament_over
from core.models import GamingSystem, MatchStatus, Player, SystemStatus
from core.storage import RecordNotFoundError, StorageManager

from web.api import build_dashboard, build_system_list
from web.server import create_server, snapshot_revision

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
async def create_tournament(
    storage: StorageManager, names: list[str]
) -> GamingSystem:
    """Create a running tournament with a seeded bracket.

    Returns:
        The persisted :class:`core.models.GamingSystem`.
    """

    system = await storage.create_system("Test Cup", max_players=len(names))
    for index, name in enumerate(names):
        player = await storage.create_player(system.id, name)
        player.link_telegram(1000 + index, f"user{index}")
        await storage.save_player(player)
        system.add_player(player.id)

    system.status = SystemStatus.RUNNING
    system.registration_open = False
    await storage.save_system(system)

    matches = build_bracket(system.id, system.player_ids)
    await storage.save_matches(matches)
    for player in await storage.list_players(system.id):
        player.mark_active()
        await storage.save_player(player)

    return system


async def finish_tournament(
    storage: StorageManager, system: GamingSystem
) -> list[Player]:
    """Play every ready match (first slot always wins) until a champion exists."""

    players = await storage.list_players(system.id)
    matches = await storage.list_matches(system.id)

    guard = 0
    while not is_tournament_over(matches, players):
        guard += 1
        if guard > 300:
            raise AssertionError("Tournament did not progress.")

        ready = sorted(
            (match for match in matches if match.status is MatchStatus.READY),
            key=lambda match: (match.round, match.position),
        )
        if not ready:
            raise AssertionError("No READY match while the tournament is unfinished.")

        match = ready[0]
        advance_match(matches, match.id, match.player1_id, players=players)
        await storage.save_matches(matches)
        for player in players:
            await storage.save_player(player)

    system.status = SystemStatus.COMPLETED
    system.registration_open = False
    await storage.save_system(system)
    return players


class StorageTestCase(unittest.IsolatedAsyncioTestCase):
    """Provide an isolated storage manager for every test."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = StorageManager(data_dir=Path(self._tmp.name))
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------
class SystemListTests(StorageTestCase):
    async def test_empty_database_returns_empty_list(self) -> None:
        payload = await build_system_list(self.storage)

        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["systems"], [])

    async def test_reports_counts_and_status(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob", "Carol", "Dave"])
        matches = await self.storage.list_matches(system.id)

        payload = await build_system_list(self.storage)

        self.assertEqual(len(payload["systems"]), 1)
        entry = payload["systems"][0]
        self.assertEqual(entry["id"], system.id)
        self.assertEqual(entry["player_count"], 4)
        self.assertEqual(entry["match_count"], len(matches))
        self.assertEqual(entry["ready_count"], 2)
        self.assertEqual(entry["status"], "running")

    async def test_newest_tournament_comes_first(self) -> None:
        first = await self.storage.create_system("First", max_players=4)
        second = await self.storage.create_system("Second", max_players=4)

        payload = await build_system_list(self.storage)

        ids = [entry["id"] for entry in payload["systems"]]
        self.assertEqual(ids, [second.id, first.id])


class DashboardTests(StorageTestCase):
    async def test_groups_and_orders_matches(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob", "Carol", "Dave"])

        payload = await build_dashboard(self.storage, system.id)

        self.assertIn("winners", payload["brackets"])
        self.assertIn("losers", payload["brackets"])
        self.assertIn("grand_final", payload["brackets"])

        for side, matches in payload["brackets"].items():
            for match in matches:
                self.assertEqual(match["bracket"], side)

        winners = payload["brackets"]["winners"]
        keys = [(match["round"], match["position"]) for match in winners]
        self.assertEqual(keys, sorted(keys))

        first_round = [match for match in winners if match["round"] == 1]
        self.assertEqual(len(first_round), 2)
        self.assertTrue(all(match["status"] == "ready" for match in first_round))

    async def test_payload_never_leaks_private_fields(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob"])
        players = await self.storage.list_players(system.id)

        payload = await build_dashboard(self.storage, system.id)
        serialized = json.dumps(payload)

        self.assertNotIn("entry_code", serialized)
        self.assertNotIn("telegram_id", serialized)
        for player in players:
            self.assertNotIn(player.entry_code, serialized)
            self.assertNotIn(str(player.telegram_id), serialized)

    async def test_champion_is_reported_after_the_final(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob", "Carol", "Dave"])
        await finish_tournament(self.storage, system)

        payload = await build_dashboard(self.storage, system.id)
        players = await self.storage.list_players(system.id)
        survivor = [
            player for player in players if player.status.value not in {"eliminated", "withdrawn"}
        ]

        self.assertEqual(len(survivor), 1)
        self.assertEqual(payload["system"]["status"], "completed")
        self.assertEqual(payload["champion"]["id"], survivor[0].id)
        self.assertEqual(payload["champion"]["name"], survivor[0].name)
        self.assertEqual(payload["counts"]["remaining"], 1)
        self.assertGreater(payload["counts"]["completed"], 0)

    async def test_running_tournament_has_no_champion(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob", "Carol", "Dave"])

        payload = await build_dashboard(self.storage, system.id)

        self.assertIsNone(payload["champion"])

    async def test_odd_roster_reports_byes_and_dead_matches(self) -> None:
        system = await create_tournament(
            self.storage, ["Alice", "Bob", "Carol", "Dave", "Eve"]
        )

        payload = await build_dashboard(self.storage, system.id)
        all_matches = [
            match for matches in payload["brackets"].values() for match in matches
        ]

        self.assertTrue(any(match["is_bye"] for match in all_matches))
        self.assertTrue(any(match["is_dead"] for match in all_matches))
        self.assertIsNone(payload["champion"])

    async def test_unknown_system_raises(self) -> None:
        with self.assertRaises(RecordNotFoundError):
            await build_dashboard(self.storage, "sys_missing")

    async def test_standings_order_matches_the_bot(self) -> None:
        system = await create_tournament(self.storage, ["Alice", "Bob", "Carol", "Dave"])
        await finish_tournament(self.storage, system)

        payload = await build_dashboard(self.storage, system.id)
        players = payload["players"]
        keys = [(player["losses"], -player["wins"], player["name"]) for player in players]

        self.assertEqual(keys, sorted(keys))
        self.assertIn(payload["champion"]["id"], {player["id"] for player in players})


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class ServerTests(StorageTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.system = await create_tournament(
            self.storage, ["Alice", "Bob", "Carol", "Dave"]
        )
        await finish_tournament(self.storage, self.system)

        self.server = create_server(
            self.storage,
            host="127.0.0.1",
            port=0,
            frontend_dir=FRONTEND_DIR,
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05),
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    async def asyncTearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        await super().asyncTearDown()

    def request(self, path: str, method: str = "GET"):
        """Perform one request and return status, headers and body."""

        request = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def get_json(self, path: str):
        status, headers, body = self.request(path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_health_endpoint(self) -> None:
        payload = self.get_json("/api/health")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "tourneycore-web")

    def test_systems_endpoint(self) -> None:
        payload = self.get_json("/api/systems")

        self.assertEqual([entry["id"] for entry in payload["systems"]], [self.system.id])

    def test_dashboard_endpoint(self) -> None:
        payload = self.get_json(f"/api/systems/{self.system.id}")

        self.assertEqual(payload["system"]["id"], self.system.id)
        self.assertEqual(payload["system"]["status"], "completed")
        self.assertIsNotNone(payload["champion"])
        self.assertTrue(payload["brackets"]["winners"])

    def test_unknown_system_returns_404(self) -> None:
        status, _, body = self.request("/api/systems/sys_missing")

        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_unknown_endpoint_returns_404(self) -> None:
        status, _, body = self.request("/api/nope")

        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_mutations_are_rejected(self) -> None:
        status, headers, body = self.request("/api/systems", method="POST")

        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "GET")
        self.assertEqual(json.loads(body)["error"], "method_not_allowed")

    def test_index_is_served(self) -> None:
        status, headers, body = self.request("/")

        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type"))
        self.assertIn(b"TourneyCore", body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy"))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_static_assets_are_served(self) -> None:
        for path, expected in (
            ("/styles.css", "text/css"),
            ("/app.js", "text/javascript"),
            ("/favicon.svg", "image/svg+xml"),
        ):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn(expected, headers.get("Content-Type"))
                self.assertTrue(body)

    def test_missing_asset_returns_404(self) -> None:
        status, _, _ = self.request("/missing.css")

        self.assertEqual(status, 404)

    def test_path_traversal_is_blocked(self) -> None:
        for path in ("/../core/models.py", "/..%2fcore%2fmodels.py"):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertEqual(status, 404)

    def test_head_returns_no_body(self) -> None:
        status, headers, body = self.request("/api/health", method="HEAD")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers.get("Content-Length")), 0)


# ---------------------------------------------------------------------------
# Live updates (Server-Sent Events)
# ---------------------------------------------------------------------------
class StreamTests(StorageTestCase):
    """Exercise the change-detection stream against a running tournament."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.system = await create_tournament(
            self.storage, ["Alice", "Bob", "Carol", "Dave"]
        )
        self.server = create_server(
            self.storage,
            host="127.0.0.1",
            port=0,
            frontend_dir=FRONTEND_DIR,
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05),
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    async def asyncTearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        await super().asyncTearDown()

    @staticmethod
    def read_event(response):
        """Read one complete SSE frame and return its lines."""

        lines = []
        while True:
            raw = response.readline()
            if not raw:
                raise AssertionError("SSE stream closed unexpectedly.")
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                return lines
            lines.append(line)

    @staticmethod
    def event_name(lines):
        for line in lines:
            if line.startswith("event:"):
                return line.split(":", 1)[1].strip()
        return None

    @staticmethod
    def event_payload(lines):
        for line in lines:
            if line.startswith("data:"):
                return json.loads(line.split(":", 1)[1].strip())
        return {}

    async def record_one_result(self) -> None:
        """Record a single READY match so the database revision changes."""

        players = await self.storage.list_players(self.system.id)
        matches = await self.storage.list_matches(self.system.id)
        match = next(
            item for item in matches if item.status is MatchStatus.READY
        )
        advance_match(matches, match.id, match.player1_id, players=players)
        await self.storage.save_matches(matches)
        for player in players:
            await self.storage.save_player(player)

    async def test_stream_sends_initial_update(self) -> None:
        response = urllib.request.urlopen(
            self.base + "/api/stream", timeout=10
        )
        try:
            self.assertIn(
                "text/event-stream", response.headers.get("Content-Type")
            )
            lines = await asyncio.to_thread(self.read_event, response)
            self.assertEqual(self.event_name(lines), "update")
            self.assertTrue(self.event_payload(lines)["revision"])
        finally:
            response.close()

    async def test_stream_reports_a_new_result(self) -> None:
        response = urllib.request.urlopen(
            self.base + "/api/stream", timeout=10
        )
        try:
            first = self.event_payload(
                await asyncio.to_thread(self.read_event, response)
            )
            await self.record_one_result()

            deadline = time.monotonic() + 8
            changed = None
            while time.monotonic() < deadline:
                lines = await asyncio.to_thread(self.read_event, response)
                if self.event_name(lines) != "update":
                    continue
                payload = self.event_payload(lines)
                if payload.get("revision") != first.get("revision"):
                    changed = payload
                    break

            self.assertIsNotNone(
                changed, "no update event was pushed after a result"
            )
        finally:
            response.close()


class RevisionTests(StorageTestCase):
    async def test_revision_changes_when_data_is_written(self) -> None:
        before = snapshot_revision(self.storage.data_dir)
        await self.storage.create_system("Cup", max_players=4)
        after = snapshot_revision(self.storage.data_dir)

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
