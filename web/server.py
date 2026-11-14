"""Read-only HTTP server for the TourneyCore mini-app.

Run it next to the bot and the admin CLI::

    python -m web.server --port 8080

Endpoints
---------
``GET /api/health``
    Liveness probe used by the frontend and by monitoring.
``GET /api/systems``
    Every tournament with aggregate counts, for the selector.
``GET /api/systems/<id>``
    The full dashboard of one tournament.
``GET /`` and ``/assets...``
    Static files from ``frontend/``.

The server is deliberately read-only: results are recorded by the admin CLI,
so a public dashboard can be exposed on the venue network without giving
anyone the ability to change the bracket.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import Coroutine, Sequence
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.storage import (
    DEFAULT_DATA_DIR,
    RecordNotFoundError,
    StorageError,
    StorageManager,
)

from web.api import build_dashboard, build_system_list

__all__ = ["TourneyCoreHandler", "create_server", "main"]

LOGGER = logging.getLogger("tourneycore.web")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRONTEND_DIR = PROJECT_ROOT / "frontend"

#: Extensions the static server is allowed to serve.
CONTENT_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}

#: Telegram embeds mini-apps in an iframe on the web client.
TELEGRAM_ORIGINS = "https://web.telegram.org https://telegram.org https://*.telegram.org"

SECURITY_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://telegram.org; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        f"frame-ancestors {TELEGRAM_ORIGINS}"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

#: JSON documents whose changes drive the live update stream.
STREAMED_FILES = ("systems.json", "users.json", "matches.json")

#: Seconds between change checks and between keep-alive comments.
STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0


def snapshot_revision(data_dir: Path) -> str:
    """Return a short fingerprint of the tournament documents.

    Only file metadata is hashed, so the check is cheap and never parses the
    database. A different revision means the frontend should refetch.
    """

    digest = hashlib.sha256()
    for name in STREAMED_FILES:
        try:
            stat = (data_dir / name).stat()
        except OSError:
            digest.update(f"{name}:missing".encode("utf-8"))
            continue
        digest.update(f"{name}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8"))
    return digest.hexdigest()[:16]


class _LoopRunner:
    """Run storage coroutines on one dedicated event loop.

    ``StorageManager`` owns :class:`asyncio.Lock` instances. Serving requests
    from a :class:`~http.server.ThreadingHTTPServer` with one ``asyncio.run``
    per thread would put those locks on competing loops, so every coroutine is
    funnelled through a single background loop instead.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="tourneycore-web-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Block until ``coro`` finishes on the shared loop."""

        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=15)

    def close(self) -> None:
        """Stop the loop, join its thread and release the loop."""

        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()


class TourneyCoreHandler(BaseHTTPRequestHandler):
    """Serve the JSON API and the static mini-app."""

    protocol_version = "HTTP/1.1"
    server_version = "TourneyCore/0.8"

    storage: StorageManager
    runner: _LoopRunner
    frontend_dir: Path

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - name required by http.server
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/api/stream", "/api/stream/"):
            self._serve_stream()
            return
        if path == "/api" or path.startswith("/api/"):
            self._handle_api(path)
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:  # noqa: N802 - name required by http.server
        """Answer HEAD exactly like GET, but without the body."""

        self._head_only = True
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def log_message(self, format: str, *args: Any) -> None:
        """Route ``http.server`` access logs through the package logger."""

        LOGGER.debug("%s - %s", self.address_string(), format % args)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _handle_api(self, path: str) -> None:
        """Dispatch a read-only API request."""

        try:
            status, payload = self._api_payload(path)
        except RecordNotFoundError as error:
            self._send_json(
                {"error": "not_found", "detail": str(error)},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        except StorageError:
            # Storage failures may contain filesystem paths; keep the response
            # generic and log the details instead.
            LOGGER.exception("Storage failure while serving %s", path)
            self._send_json(
                {"error": "storage_error", "detail": "Tournament data is unavailable."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(payload, status=status)

    def _api_payload(self, path: str) -> tuple[HTTPStatus, Any]:
        """Resolve ``path`` into a status and a JSON payload."""

        normalized = path.rstrip("/") or "/"
        if normalized in ("/api", "/api/health"):
            return HTTPStatus.OK, {
                "status": "ok",
                "service": "tourneycore-web",
            }
        if normalized == "/api/systems":
            return HTTPStatus.OK, self.runner.call(build_system_list(self.storage))

        parts = [part for part in normalized.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "systems"]:
            return HTTPStatus.OK, self.runner.call(
                build_dashboard(self.storage, parts[2])
            )

        return HTTPStatus.NOT_FOUND, {
            "error": "not_found",
            "detail": f"Unknown endpoint {path!r}.",
        }

    # ------------------------------------------------------------------
    # Live updates (Server-Sent Events)
    # ------------------------------------------------------------------
    def _serve_stream(self) -> None:
        """Push an ``update`` event whenever the database changes."""

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._send_security_headers()
        self.end_headers()

        # SSE responses carry no Content-Length: the connection stays open and
        # ends when the client goes away or the server stops.
        self.close_connection = True

        data_dir = self.storage.data_dir
        revision: str | None = None
        last_heartbeat = time.monotonic()

        try:
            while not self.server.stop_event.is_set():
                current = snapshot_revision(data_dir)
                if current != revision:
                    revision = current
                    self._write_sse(
                        "update",
                        {
                            "revision": revision,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    last_heartbeat = time.monotonic()
                elif (
                    time.monotonic() - last_heartbeat
                    >= STREAM_HEARTBEAT_SECONDS
                ):
                    self._write_sse("ping", {"revision": revision})
                    last_heartbeat = time.monotonic()
                self.server.stop_event.wait(STREAM_POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The browser closed the tab; nothing left to do.
            return

    def _write_sse(self, event: str, payload: dict[str, Any]) -> None:
        """Write one SSE frame and flush it immediately."""

        frame = (
            f"event: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        ).encode("utf-8")
        self.wfile.write(frame)
        self.wfile.flush()

    # ------------------------------------------------------------------
    # Static files
    # ------------------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        """Serve a file from ``frontend/``, guarding against traversal."""

        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        root = self.frontend_dir.resolve()
        candidate = (root / relative).resolve()

        if not candidate.is_relative_to(root) or candidate == root:
            self._send_error_page(HTTPStatus.NOT_FOUND, "File not found.")
            return
        if not candidate.is_file():
            self._send_error_page(HTTPStatus.NOT_FOUND, "File not found.")
            return

        content_type = CONTENT_TYPES.get(candidate.suffix.lower())
        if content_type is None:
            self._send_error_page(HTTPStatus.NOT_FOUND, "File not found.")
            return

        try:
            body = candidate.read_bytes()
        except OSError:
            LOGGER.exception("Cannot read static file %s", candidate)
            self._send_error_page(
                HTTPStatus.INTERNAL_SERVER_ERROR, "Cannot read file."
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._send_security_headers()
        self.end_headers()
        self._write(body)

    def _send_error_page(self, status: HTTPStatus, message: str) -> None:
        """Send a small HTML error page for static requests."""

        body = (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{status.value}</title>"
            f"<h1>{status.value} {status.phrase}</h1><p>{message}</p>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self._write(body)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _send_json(self, payload: Any, *, status: HTTPStatus) -> None:
        """Send ``payload`` as UTF-8 JSON."""

        body = json.dumps(
            payload, ensure_ascii=False, indent=2
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self._write(body)

    def _method_not_allowed(self) -> None:
        """Answer any mutating verb with ``405 Method Not Allowed``."""

        body = json.dumps(
            {"error": "method_not_allowed", "detail": "This API is read-only."}
        ).encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self._write(body)

    def _send_security_headers(self) -> None:
        """Attach the hardening headers to every response."""

        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)

    def _write(self, body: bytes) -> None:
        """Write a response body, skipping it for a HEAD request."""

        if not getattr(self, "_head_only", False):
            self.wfile.write(body)


class TourneyCoreHTTPServer(ThreadingHTTPServer):
    """Threaded server that owns the storage event loop."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[TourneyCoreHandler],
        *,
        runner: _LoopRunner,
    ) -> None:
        super().__init__(address, handler)
        self.runner = runner
        self.stop_event = threading.Event()

    def server_close(self) -> None:
        """Close the socket, stop open streams and the storage loop."""

        self.stop_event.set()
        super().server_close()
        self.runner.close()


def create_server(
    storage: StorageManager,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    frontend_dir: Path | None = None,
) -> TourneyCoreHTTPServer:
    """Build (but do not start) an HTTP server for ``storage``.

    Args:
        storage: Initialized storage manager.
        host: Interface to bind.
        port: TCP port; ``0`` lets the operating system pick a free one.
        frontend_dir: Static asset directory; defaults to ``frontend/``.

    Returns:
        A configured, ready-to-serve :class:`TourneyCoreHTTPServer`.
    """

    runner = _LoopRunner()
    assets = (frontend_dir or DEFAULT_FRONTEND_DIR).resolve()

    handler = type(
        "BoundTourneyCoreHandler",
        (TourneyCoreHandler,),
        {
            "storage": storage,
            "runner": runner,
            "frontend_dir": assets,
        },
    )
    try:
        return TourneyCoreHTTPServer((host, port), handler, runner=runner)
    except OSError:
        # Binding failed (for example the port is busy): do not leak the loop
        # thread.
        runner.close()
        raise


def build_parser() -> argparse.ArgumentParser:
    """Create the launcher argument parser."""

    parser = argparse.ArgumentParser(prog="tourneycore-web")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind.")
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"JSON data directory (default: {DEFAULT_DATA_DIR}).",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=DEFAULT_FRONTEND_DIR,
        help=f"Static assets directory (default: {DEFAULT_FRONTEND_DIR}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start the mini-app server until interrupted."""

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    storage = StorageManager(data_dir=args.data_dir)

    server = create_server(
        storage,
        host=args.host,
        port=args.port,
        frontend_dir=args.frontend_dir,
    )
    server.runner.call(storage.initialize())

    host, port = server.server_address[:2]
    LOGGER.info("Serving the mini-app on http://%s:%s", host, port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Mini-app server stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
