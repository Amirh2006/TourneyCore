"""Asynchronous, file-based JSON persistence for TourneyCore.

The project intentionally avoids a heavyweight database. All runtime state is
kept in three UTF-8 JSON documents inside the data directory:

* ``systems.json`` - :class:`~core.models.GamingSystem` records.
* ``users.json``   - :class:`~core.models.Player` records.
* ``matches.json`` - :class:`~core.models.Match` records.

Design goals
------------
Atomicity
    Every write produces a uniquely named temporary file which is flushed,
    ``fsync``-ed and then swapped into place with :func:`os.replace`. A crash
    therefore leaves either the previous or the new document on disk, never a
    half-written one.

Non-blocking I/O
    File reads and writes go through :mod:`aiofiles`. Blocking primitives
    (``os.replace``, ``os.fsync``, ``mkdir``) are offloaded with
    :func:`asyncio.to_thread` so the event loop is never stalled.

Consistency
    Each collection owns an :class:`asyncio.Lock`. Read-modify-write cycles are
    serialized inside a process, which prevents lost updates when the Telegram
    bot and the CLI admin tool touch the same document concurrently.

Validation
    Documents are validated against the Pydantic models from
    :mod:`core.models`. Invalid JSON or schema violations raise explicit
    :class:`StorageError` subclasses instead of silently corrupting state.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

import aiofiles
from pydantic import BaseModel, ValidationError

from core.models import GamingSystem, Match, Player, generate_entry_code

__all__ = [
    "CorruptDatabaseError",
    "DatabaseValidationError",
    "DuplicateEntryCodeError",
    "JsonCollection",
    "RecordNotFoundError",
    "StorageError",
    "StorageManager",
    "atomic_write_text",
    "DEFAULT_DATA_DIR",
    "MATCHES_FILE",
    "PLAYERS_FILE",
    "SYSTEMS_FILE",
]

#: Root directory of the project package (``TourneyCore/``).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: Default location of the JSON database.
DEFAULT_DATA_DIR: Path = PROJECT_ROOT / "data"

#: File names of the three collections.
SYSTEMS_FILE: str = "systems.json"
PLAYERS_FILE: str = "users.json"
MATCHES_FILE: str = "matches.json"

#: Indentation used when serializing documents, for human friendly diffs.
JSON_INDENT: int = 2

#: Generic Pydantic model bound used by :class:`JsonCollection`.
ModelT = TypeVar("ModelT", bound=BaseModel)


class StorageError(Exception):
    """Base class for every persistence related failure."""


class CorruptDatabaseError(StorageError):
    """Raised when a document is not valid JSON.

    Attributes:
        path: Absolute path of the offending document.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Corrupt database file {path}: {reason}")


class DatabaseValidationError(StorageError):
    """Raised when a document does not match its Pydantic schema.

    Attributes:
        path: Absolute path of the offending document.
        errors: Pydantic validation details, including record indexes.
    """

    def __init__(self, path: Path, errors: Sequence[dict[str, Any]]) -> None:
        self.path = path
        self.errors = list(errors)
        super().__init__(
            f"Invalid records in {path}: {len(self.errors)} validation error(s)."
        )


class RecordNotFoundError(StorageError):
    """Raised when a required record is missing from a collection."""


class DuplicateEntryCodeError(StorageError):
    """Raised when two players would share the same entry code."""


async def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    The payload is written to a uniquely named sibling file, flushed, synced to
    disk and then swapped in with :func:`os.replace`. Readers therefore see
    either the complete previous content or the complete new content.

    Args:
        path: Destination document path.
        text: Full document content to persist.
    """

    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")

    try:
        async with aiofiles.open(
            temp_path, mode="w", encoding="utf-8", newline="\n"
        ) as handle:
            await handle.write(text)
            await handle.flush()
            await asyncio.to_thread(os.fsync, handle.fileno())
        await asyncio.to_thread(os.replace, temp_path, path)
    except BaseException:
        with suppress(OSError):
            await asyncio.to_thread(temp_path.unlink, True)
        raise


async def read_text_if_exists(path: Path) -> str | None:
    """Return the content of ``path`` or ``None`` when it does not exist."""

    try:
        async with aiofiles.open(path, mode="r", encoding="utf-8") as handle:
            return await handle.read()
    except FileNotFoundError:
        return None


class JsonCollection(Generic[ModelT]):
    """A single JSON document holding a homogeneous list of models.

    The collection is the lowest persistence unit of the project. It owns the
    file path, the Pydantic model used for validation and an
    :class:`asyncio.Lock` that serializes read-modify-write cycles.

    Args:
        path: Location of the JSON document.
        model: Pydantic model class used to validate every record.
        quarantine_on_corruption: When ``True``, a document that is not valid
            JSON is renamed aside and treated as empty instead of raising.
            Disabled by default so that corrupt state is never lost silently.
        lock: Optional shared lock, useful for tests or multi-collection
            transactions.
    """

    def __init__(
        self,
        path: Path,
        model: type[ModelT],
        *,
        quarantine_on_corruption: bool = False,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self._path = path
        self._model = model
        self._quarantine_on_corruption = quarantine_on_corruption
        self._lock = lock if lock is not None else asyncio.Lock()

    @property
    def path(self) -> Path:
        """Location of the backing JSON document."""

        return self._path

    @property
    def model(self) -> type[ModelT]:
        """Pydantic model class used to validate records."""

        return self._model

    @property
    def lock(self) -> asyncio.Lock:
        """Lock that serializes mutations of this collection."""

        return self._lock

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def serialize(items: Sequence[BaseModel]) -> str:
        """Serialize models to a pretty-printed JSON array with a newline."""

        payload = [item.model_dump(mode="json") for item in items]
        return json.dumps(payload, ensure_ascii=False, indent=JSON_INDENT) + "\n"

    def _validate_payload(self, payload: list[Any]) -> list[ModelT]:
        """Validate a decoded JSON array against the collection model.

        Raises:
            DatabaseValidationError: If one or more records are invalid.
        """

        items: list[ModelT] = []
        errors: list[dict[str, Any]] = []

        for index, raw in enumerate(payload):
            if not isinstance(raw, dict):
                errors.append(
                    {
                        "index": index,
                        "loc": (index,),
                        "msg": "Record must be a JSON object.",
                        "type": "type_error.object",
                    }
                )
                continue
            try:
                items.append(self._model.model_validate(raw))
            except ValidationError as exc:
                for error in exc.errors():
                    errors.append({"index": index, **error})

        if errors:
            raise DatabaseValidationError(self._path, errors)
        return items

    # ------------------------------------------------------------------
    # Internal unlocked primitives
    # ------------------------------------------------------------------
    async def _read_unlocked(self) -> list[ModelT]:
        """Read and validate the document without acquiring the lock."""

        raw = await read_text_if_exists(self._path)
        if raw is None or not raw.strip():
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            if self._quarantine_on_corruption:
                await self._quarantine_file()
                return []
            raise CorruptDatabaseError(self._path, str(exc)) from exc

        if not isinstance(payload, list):
            if self._quarantine_on_corruption:
                await self._quarantine_file()
                return []
            raise CorruptDatabaseError(
                self._path, "Top-level JSON value must be an array."
            )

        return self._validate_payload(payload)

    async def _write_unlocked(self, items: Sequence[ModelT]) -> None:
        """Atomically persist ``items`` without acquiring the lock."""

        await atomic_write_text(self._path, self.serialize(items))

    async def _quarantine_file(self) -> Path | None:
        """Move a corrupt document aside so the collection can restart empty.

        Returns:
            The new path of the quarantined file, or ``None`` when the document
            did not exist.
        """

        if not await asyncio.to_thread(self._path.exists):
            return None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self._path.with_name(
            f"{self._path.stem}.corrupt-{stamp}{self._path.suffix}"
        )
        await asyncio.to_thread(os.replace, self._path, target)
        return target

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def read_all(self) -> list[ModelT]:
        """Return every record in the document, validated against the model."""

        async with self._lock:
            return await self._read_unlocked()

    async def write_all(self, items: Sequence[ModelT]) -> None:
        """Replace the whole document with ``items`` atomically."""

        async with self._lock:
            await self._write_unlocked(items)

    async def update(
        self, mutator: Callable[[list[ModelT]], list[ModelT]]
    ) -> list[ModelT]:
        """Apply ``mutator`` to the current records and persist the result.

        The entire read-modify-write cycle runs under the collection lock, so
        concurrent callers cannot lose updates. ``mutator`` may raise to abort
        the transaction; nothing is written in that case.

        Args:
            mutator: Pure function that receives the current records and
                returns the new list to persist.

        Returns:
            The list returned by ``mutator``.
        """

        async with self._lock:
            current = await self._read_unlocked()
            updated = mutator(current)
            await self._write_unlocked(updated)
            return updated

    async def find_one(self, identifier: str, *, key: str = "id") -> ModelT | None:
        """Return the first record whose ``key`` equals ``identifier``."""

        for item in await self.read_all():
            if getattr(item, key, None) == identifier:
                return item
        return None

    async def add(self, item: ModelT) -> ModelT:
        """Append ``item`` to the document."""

        await self.update(lambda items: [*items, item])
        return item

    async def upsert(self, item: ModelT, *, key: str = "id") -> ModelT:
        """Insert ``item`` or replace the record sharing the same ``key``."""

        identifier = getattr(item, key)

        def _mutate(items: list[ModelT]) -> list[ModelT]:
            for index, existing in enumerate(items):
                if getattr(existing, key) == identifier:
                    replaced = list(items)
                    replaced[index] = item
                    return replaced
            return [*items, item]

        await self.update(_mutate)
        return item

    async def remove(self, identifier: str, *, key: str = "id") -> bool:
        """Delete the record matching ``identifier``.

        Returns:
            ``True`` when a record was removed, ``False`` otherwise.
        """

        removed = False

        def _mutate(items: list[ModelT]) -> list[ModelT]:
            nonlocal removed
            remaining = [item for item in items if getattr(item, key) != identifier]
            removed = len(remaining) != len(items)
            return remaining

        await self.update(_mutate)
        return removed

    async def clear(self) -> None:
        """Persist an empty document."""

        await self.write_all([])

    async def ensure_document(self) -> None:
        """Create an empty JSON array when the document is missing."""

        async with self._lock:
            exists = await asyncio.to_thread(self._path.exists)
            if not exists:
                await self._write_unlocked([])


class StorageManager:
    """High-level facade over the three JSON collections.

    The manager wires the collections to concrete model classes and exposes
    intention-revealing operations used by the CLI admin tools, the Telegram
    bot and the frontend exporter:

    * gaming systems - list, create, read, save, delete;
    * players - list, create with a unique entry code, redeem a code, save;
    * matches - list, read, save (single or bulk), delete.

    It never caches documents: every read hits the disk, so the bot and the CLI
    always observe each other's writes. Mutations are serialized per file by
    :class:`JsonCollection`.

    Args:
        data_dir: Directory containing the JSON documents.
        quarantine_on_corruption: Forwarded to every collection. When enabled,
            malformed documents are renamed aside instead of raising.
    """

    def __init__(
        self,
        data_dir: Path | str = DEFAULT_DATA_DIR,
        *,
        quarantine_on_corruption: bool = False,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._systems: JsonCollection[GamingSystem] = JsonCollection(
            self._data_dir / SYSTEMS_FILE,
            GamingSystem,
            quarantine_on_corruption=quarantine_on_corruption,
        )
        self._players: JsonCollection[Player] = JsonCollection(
            self._data_dir / PLAYERS_FILE,
            Player,
            quarantine_on_corruption=quarantine_on_corruption,
        )
        self._matches: JsonCollection[Match] = JsonCollection(
            self._data_dir / MATCHES_FILE,
            Match,
            quarantine_on_corruption=quarantine_on_corruption,
        )

    @property
    def data_dir(self) -> Path:
        """Directory holding the JSON documents."""

        return self._data_dir

    @property
    def systems(self) -> JsonCollection[GamingSystem]:
        """Raw collection of gaming systems."""

        return self._systems

    @property
    def players(self) -> JsonCollection[Player]:
        """Raw collection of players."""

        return self._players

    @property
    def matches(self) -> JsonCollection[Match]:
        """Raw collection of matches."""

        return self._matches

    async def initialize(self) -> None:
        """Create the data directory and any missing document."""

        await asyncio.to_thread(self._data_dir.mkdir, parents=True, exist_ok=True)
        await self._systems.ensure_document()
        await self._players.ensure_document()
        await self._matches.ensure_document()

    # ------------------------------------------------------------------
    # Gaming systems
    # ------------------------------------------------------------------
    async def list_systems(self) -> list[GamingSystem]:
        """Return every gaming system."""

        return await self._systems.read_all()

    async def get_system(self, system_id: str) -> GamingSystem | None:
        """Return a gaming system by identifier, or ``None``."""

        return await self._systems.find_one(system_id)

    async def require_system(self, system_id: str) -> GamingSystem:
        """Return a gaming system or raise :class:`RecordNotFoundError`."""

        system = await self.get_system(system_id)
        if system is None:
            raise RecordNotFoundError(f"Gaming system {system_id!r} does not exist.")
        return system

    async def create_system(
        self,
        name: str,
        *,
        description: str = "",
        max_players: int = 16,
    ) -> GamingSystem:
        """Create and persist a new gaming system."""

        system = GamingSystem(
            name=name,
            description=description,
            max_players=max_players,
        )
        await self._systems.add(system)
        return system

    async def save_system(self, system: GamingSystem) -> GamingSystem:
        """Insert or replace a gaming system."""

        return await self._systems.upsert(system)

    async def delete_system(self, system_id: str) -> bool:
        """Delete a gaming system by identifier."""

        return await self._systems.remove(system_id)

    # ------------------------------------------------------------------
    # Players
    # ------------------------------------------------------------------
    async def list_players(self, system_id: str | None = None) -> list[Player]:
        """Return players, optionally filtered by gaming system."""

        players = await self._players.read_all()
        if system_id is None:
            return players
        return [player for player in players if player.system_id == system_id]

    async def get_player(self, player_id: str) -> Player | None:
        """Return a player by identifier, or ``None``."""

        return await self._players.find_one(player_id)

    async def require_player(self, player_id: str) -> Player:
        """Return a player or raise :class:`RecordNotFoundError`."""

        player = await self.get_player(player_id)
        if player is None:
            raise RecordNotFoundError(f"Player {player_id!r} does not exist.")
        return player

    async def find_player_by_entry_code(self, entry_code: str) -> Player | None:
        """Look up a player by entry code, ignoring case and separators."""

        normalized = entry_code.strip().upper().replace("-", "").replace(" ", "")
        for player in await self._players.read_all():
            if player.entry_code == normalized:
                return player
        return None

    async def create_player(
        self,
        system_id: str,
        name: str,
        *,
        entry_code: str | None = None,
    ) -> Player:
        """Create a pending player with a unique entry code.

        Args:
            system_id: Gaming system the player belongs to.
            name: Display name of the player.
            entry_code: Explicit code, or ``None`` to generate one.

        Raises:
            DuplicateEntryCodeError: If the code is already in use.
        """

        await self.require_system(system_id)
        candidate = Player(
            system_id=system_id,
            name=name,
            entry_code=entry_code or generate_entry_code(),
        )

        def _mutate(items: list[Player]) -> list[Player]:
            used_codes = {player.entry_code for player in items}
            attempts = 0
            while candidate.entry_code in used_codes:
                if entry_code is not None:
                    raise DuplicateEntryCodeError(
                        f"Entry code {candidate.entry_code!r} is already in use."
                    )
                attempts += 1
                if attempts > 10:
                    raise StorageError(
                        "Unable to generate a unique entry code, try again."
                    )
                candidate.entry_code = generate_entry_code()
            return [*items, candidate]

        await self._players.update(_mutate)
        return candidate

    async def save_player(self, player: Player) -> Player:
        """Insert or replace a player."""

        return await self._players.upsert(player)

    async def delete_player(self, player_id: str) -> bool:
        """Delete a player by identifier."""

        return await self._players.remove(player_id)

    async def redeem_entry_code(
        self,
        entry_code: str,
        telegram_id: int,
        username: str | None = None,
    ) -> Player:
        """Pair a Telegram account with a pending player.

        This is Scenario A registration: the player types the code generated by
        the administrator into the Telegram bot, and the bot calls this method
        with the sender's identity.

        Args:
            entry_code: Code printed on the player's ticket.
            telegram_id: Telegram user id of the sender.
            username: Optional Telegram username without the leading '@'.

        Raises:
            RecordNotFoundError: If no player owns the code.
            StorageError: If the code was already redeemed or the Telegram
                account is already linked to another player.
        """

        normalized = entry_code.strip().upper().replace("-", "").replace(" ", "")

        def _mutate(items: list[Player]) -> list[Player]:
            for index, player in enumerate(items):
                if player.entry_code != normalized:
                    continue
                if player.is_registered:
                    raise StorageError(
                        f"Entry code {normalized!r} was already redeemed."
                    )
                if any(
                    other.telegram_id == telegram_id
                    for other in items
                    if other.id != player.id
                ):
                    raise StorageError(
                        f"Telegram account {telegram_id} is already linked to "
                        "another player."
                    )
                player.link_telegram(telegram_id, username)
                updated = list(items)
                updated[index] = player
                return updated
            raise RecordNotFoundError(f"Unknown entry code {normalized!r}.")

        updated_players = await self._players.update(_mutate)
        for player in updated_players:
            if player.entry_code == normalized:
                return player
        raise RecordNotFoundError(f"Unknown entry code {normalized!r}.")

    # ------------------------------------------------------------------
    # Matches
    # ------------------------------------------------------------------
    async def list_matches(self, system_id: str | None = None) -> list[Match]:
        """Return matches, optionally filtered by gaming system."""

        matches = await self._matches.read_all()
        if system_id is None:
            return matches
        return [match for match in matches if match.system_id == system_id]

    async def get_match(self, match_id: str) -> Match | None:
        """Return a match by identifier, or ``None``."""

        return await self._matches.find_one(match_id)

    async def require_match(self, match_id: str) -> Match:
        """Return a match or raise :class:`RecordNotFoundError`."""

        match = await self.get_match(match_id)
        if match is None:
            raise RecordNotFoundError(f"Match {match_id!r} does not exist.")
        return match

    async def save_match(self, match: Match) -> Match:
        """Insert or replace a single match."""

        return await self._matches.upsert(match)

    async def save_matches(self, matches: Sequence[Match]) -> None:
        """Replace the whole match document, used after bracket generation."""

        await self._matches.write_all(matches)

    async def delete_match(self, match_id: str) -> bool:
        """Delete a match by identifier."""

        return await self._matches.remove(match_id)

    async def delete_matches_for_system(self, system_id: str) -> int:
        """Delete every match of a gaming system and return the count."""

        removed = 0

        def _mutate(items: list[Match]) -> list[Match]:
            nonlocal removed
            remaining = [match for match in items if match.system_id != system_id]
            removed = len(items) - len(remaining)
            return remaining

        await self._matches.update(_mutate)
        return removed

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    async def export_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a JSON-serializable snapshot of the whole database.

        The web frontend consumes this structure directly, which keeps the
        read model decoupled from the persistence layer.
        """

        systems = await self._systems.read_all()
        players = await self._players.read_all()
        matches = await self._matches.read_all()
        return {
            "systems": [item.model_dump(mode="json") for item in systems],
            "players": [item.model_dump(mode="json") for item in players],
            "matches": [item.model_dump(mode="json") for item in matches],
        }
