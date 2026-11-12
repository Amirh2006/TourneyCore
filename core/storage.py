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
