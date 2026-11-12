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

from core.models import GamingSystem, Match, Player

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
