"""A local web UI for looking at what the batch did.

This is the CLI's stated purpose -- "so a human can look at what the batch did
and disagree with it" -- rendered in a browser instead of a terminal. It adds
no dependency: the server is :mod:`http.server` from the standard library, and
the single-page frontend ships as one self-contained HTML file with no external
asset, so the browser never reaches the network either.

The server binds to loopback only. The egress guard patches *outbound*
``connect``; accepting an inbound connection on ``127.0.0.1`` is untouched by
it, so the UI works while every outbound call a request might trigger stays
blocked -- except loopback to the local model server, exactly as on the CLI.

Two layers live here on purpose:

* :func:`dispatch` is a pure function -- ``(method, path, query, body)`` in, a
  :class:`Response` out -- so the whole API can be tested without opening a
  socket, which is what keeps the test suite offline.
* :class:`_Handler` and :func:`run_server` are the thin socket shell around it.

Access is gated by two flags, so a default server stays a safe read-only window.
``--write`` unlocks adding source entries to the vault; ``--dev`` unlocks the
pipeline, graph and test routes and implies ``--write``. A route that needs a
capability the server was not started with refuses with 403.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import parse_qs, urlsplit

from diairy.cli.context import AppContext, build_context
from diairy.core.clock import ensure_aware, utc_now
from diairy.core.config import Settings, load_settings
from diairy.core.egress import install_egress_guard
from diairy.core.errors import DiairyError, ProviderError
from diairy.store.graph import graph_backend_available
from diairy.vault.writer import write_attachment, write_entry

_MAX_AUDIO_BYTES = 100 * 1024 * 1024
"""A recorded note is minutes of speech, not a media library. Refuse more."""

ContextFactory = Callable[..., AppContext]
"""How a request obtains its wiring. ``build_context`` in production, a fake in tests."""

PIPELINE_STAGES = ("ingest", "process", "embed", "project")
"""The stages a developer may trigger from the UI, in run order."""

_TEST_TIMEOUT_SECONDS = 600
"""The developer test runner is a nightly-scale operation, not interactive."""


@dataclass(frozen=True)
class Response:
    """A ready-to-serialise HTTP response, independent of the socket layer."""

    status: int
    payload: Any = None
    content_type: str = "application/json"
    raw: bytes | None = None

    def body(self) -> bytes:
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload, ensure_ascii=False, default=str).encode("utf-8")


def _error(message: str, status: int) -> Response:
    """An error the frontend can display verbatim -- the message says what to do."""
    return Response(status=status, payload={"error": message})


def _index_html() -> bytes:
    """The single-page app, read from the package's data files."""
    resource = files("diairy.cli") / "static" / "index.html"
    return resource.read_bytes()


# -- read-only handlers -----------------------------------------------------


def _status_payload(ctx: AppContext) -> dict[str, Any]:
    repo = ctx.repository
    current = repo.fact_count()
    return {
        "vault": str(ctx.settings.vault_path),
        "store": str(ctx.settings.db_path),
        "profile": ctx.settings.model_profile,
        "documents": repo.document_count(),
        "chunks": repo.chunk_count(),
        "chunks_awaiting_extraction": len(repo.chunks_without_facts()),
        "current_facts": current,
        "superseded_facts": repo.fact_count(current_only=False) - current,
        "concepts": repo.concept_count(),
        "vectors_indexed": ctx.search.indexed_vector_count(),
        "graph_backend": graph_backend_available(),
    }


def _profile_payload(ctx: AppContext, *, dev: bool, write: bool) -> dict[str, Any]:
    """What the Dev panel shows: models, paths, and the guard's state."""
    return {
        "dev": dev,
        "write": write,
        "profile": ctx.settings.model_profile,
        "extraction_model": ctx.profile.extraction.model,
        "extraction_backend": ctx.profile.extraction.backend,
        "embedding_model": ctx.profile.embedding.model,
        "embedding_backend": ctx.profile.embedding.backend,
        "transcription_backend": (
            ctx.profile.transcription.backend if ctx.profile.transcription else None
        ),
        "transcription_model": (
            ctx.profile.transcription.model if ctx.profile.transcription else None
        ),
        "vault_path": str(ctx.settings.vault_path),
        "data_dir": str(ctx.settings.data_dir),
        "db_path": str(ctx.settings.db_path),
        "graph_path": str(ctx.settings.graph_path),
        "ollama_base_url": ctx.settings.ollama_base_url,
        "egress": "loopback only (local model server)",
        "graph_backend": graph_backend_available(),
    }


def _runs_payload(ctx: AppContext) -> dict[str, Any]:
    return {
        "runs": [
            {
                "id": run.id,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "model": run.model,
                "profile": run.profile,
                "stats": run.stats,
                "error": run.error,
            }
            for run in ctx.repository.recent_runs(limit=20)
        ]
    }


def _passage_dict(passage: Any) -> dict[str, Any]:
    return {
        "index": passage.index,
        "chunk_id": passage.chunk_id,
        "text": passage.text,
        "source_path": passage.source_path,
        "event_date": passage.event_date,
        "score": passage.score,
        "found_by_both": passage.found_by_both,
    }


def _search_payload(ctx: AppContext, query: str, limit: int) -> dict[str, Any]:
    context = ctx.retriever().retrieve(query, limit=limit)
    return {
        "query": query,
        "semantic_search_used": context.semantic_search_used,
        "passages": [_passage_dict(passage) for passage in context.passages],
    }


def _ask_payload(ctx: AppContext, question: str, limit: int) -> dict[str, Any]:
    answer = ctx.answerer().ask(question, limit=limit)
    return {
        "question": question,
        "text": answer.text,
        "is_uncited": answer.is_uncited,
        "semantic_search_used": answer.semantic_search_used,
        "cited_indexes": sorted(answer.cited_indexes),
        "passages": [_passage_dict(passage) for passage in answer.passages],
    }


def _chunk_payload(ctx: AppContext, chunk_id: str) -> Response:
    chunk = ctx.repository.get_chunk(chunk_id)
    if chunk is None:
        return _error(f"No chunk with id {chunk_id}. Copy one from a search result.", 404)
    facts = ctx.repository.facts_for_chunk(chunk.id, current_only=False)
    return Response(
        status=200,
        payload={
            "chunk": {
                "id": chunk.id,
                "text": chunk.text,
                "heading_path": list(chunk.heading_path),
            },
            "facts": [
                {
                    "subject": {"label": fact.subject.label, "type": fact.subject.type},
                    "predicate": fact.predicate,
                    "object": {"label": fact.object.label, "type": fact.object.type},
                    "object_is_literal": fact.object_is_literal,
                    "confidence": fact.confidence,
                    "quote": fact.evidence.quote,
                    "is_current": fact.is_current,
                }
                for fact in facts
            ],
        },
    )


# -- write handlers (require --write) ---------------------------------------


def _parse_event_time(raw: object) -> datetime:
    """Read the date the user set, defaulting to now if they left it blank.

    Accepts a bare ``YYYY-MM-DD`` or a full ISO timestamp; anything else is a
    mistake worth reporting rather than silently backdating to today.
    """
    if raw in (None, ""):
        return utc_now()
    if not isinstance(raw, str):
        raise DiairyError("The date must be text, as YYYY-MM-DD.")
    try:
        return ensure_aware(datetime.fromisoformat(raw))
    except ValueError as exc:
        raise DiairyError(f"{raw!r} is not a date. Use YYYY-MM-DD.") from exc


def _create_entry_payload(ctx: AppContext, body: dict[str, Any]) -> Response:
    text = str(body.get("body", ""))
    title_raw = body.get("title")
    title = str(title_raw).strip() if title_raw not in (None, "") else None
    audio_raw = body.get("audio")
    audio = str(audio_raw).strip() if audio_raw not in (None, "") else None
    event_time = _parse_event_time(body.get("date"))

    entry = write_entry(
        ctx.settings.vault_path, body=text, event_time=event_time, title=title, audio=audio
    )
    return Response(
        status=201,
        payload={
            "relative_path": entry.relative_path,
            "event_time": entry.event_time,
            "hint": "Run ingest to record it, then process to extract facts.",
        },
    )


def _transcribe_payload(
    ctx: AppContext, audio: bytes, *, language: str | None, suffix: str
) -> Response:
    """Transcribe a recording, keep the audio, and return the text for review.

    The audio is transcribed from a throwaway temp file first; only on success
    is the recording persisted as a vault attachment. That way a failed
    transcription -- a missing model, an empty upload -- never leaves an
    orphaned blob behind. The transcript is *not* saved as an entry here: the
    frontend drops it into the editor so the user reviews it before saving,
    which is when the returned ``audio`` path gets linked. See ADR 0009.
    """
    if ctx.transcriber is None:
        return _error(
            "This profile has no transcription model. Pick a profile whose "
            "registry entry defines one, or add a [transcription] section.",
            503,
        )
    if not audio:
        return _error("No audio was received. Record something, then try again.", 400)

    with NamedTemporaryFile(suffix=f".{suffix}", delete=True) as handle:
        handle.write(audio)
        handle.flush()
        try:
            text = ctx.transcriber.transcribe(handle.name, language=language)
        except ProviderError as exc:
            # A missing dependency or undownloaded weights: the user's machine is
            # not ready, not a bad request. The message already says what to run.
            return _error(str(exc), 503)

    attachment = write_attachment(
        ctx.settings.vault_path, audio, event_time=utc_now(), suffix=suffix
    )
    return Response(
        status=200,
        payload={
            "text": text,
            "audio": attachment.relative_path,
            "language": language,
        },
    )


def _ingest_payload(ctx: AppContext) -> Response:
    """Register new and changed files. No model, so it is safe outside --dev."""
    return Response(
        status=200, payload={"stage": "ingest", "stats": ctx.pipeline().ingest().as_dict()}
    )


# -- developer handlers (require --dev) -------------------------------------


def _pipeline_payload(ctx: AppContext, stage: str, limit: int | None) -> Response:
    pipeline = ctx.pipeline()
    if stage == "ingest":
        return Response(status=200, payload={"stage": stage, "stats": pipeline.ingest().as_dict()})
    if stage == "process":
        return Response(
            status=200, payload={"stage": stage, "stats": pipeline.process(limit=limit).as_dict()}
        )
    if stage == "embed":
        result = pipeline.embed()
        return Response(
            status=200,
            payload={
                "stage": stage,
                "stats": {"chunks_embedded": result.chunks_embedded},
                "available": result.available,
                "note": result.note,
            },
        )
    if stage == "project":
        stats = pipeline.project()
        return Response(
            status=200,
            payload={
                "stage": stage,
                "stats": {"concepts": stats.concepts, "edges": stats.edges},
            },
        )
    return _error(f"Unknown stage {stage!r}. One of: {', '.join(PIPELINE_STAGES)}.", 400)


def _neighbours_payload(ctx: AppContext, label: str) -> Response:
    if ctx.graph is None:
        return _error("The graph backend is not available on this machine.", 503)
    concept = ctx.repository.find_concept_by_label(label)
    if concept is None:
        return _error(f"No concept labelled {label!r}. Try one from the facts.", 404)
    return Response(
        status=200,
        payload={
            "concept": {"id": concept.id, "label": concept.label, "type": concept.type},
            "neighbours": ctx.graph.neighbours(concept.id),
        },
    )


def _run_test_suite() -> Response:
    """Run the offline test suite and hand back its summary.

    The suite blocks the network on itself, so this never reaches out; it is a
    developer convenience for checking the tree is green without leaving the UI.
    """
    root = _repository_root()
    try:
        completed = subprocess.run(
            ["uv", "run", "pytest", "-q"],  # noqa: S607 -- fixed argv, local, dev-triggered
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return _error("Could not find `uv`. Install it, or run `uv run pytest` yourself.", 500)
    except subprocess.TimeoutExpired:
        return _error(
            f"The test suite ran longer than {_TEST_TIMEOUT_SECONDS}s and was stopped.", 504
        )
    tail = (completed.stdout or completed.stderr).splitlines()[-40:]
    return Response(
        status=200,
        payload={
            "passed": completed.returncode == 0,
            "return_code": completed.returncode,
            "output": "\n".join(tail),
        },
    )


def _repository_root() -> Path:
    """Walk up from this file to the workspace root (the one with pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return parent
    return here.parent


# -- routing ----------------------------------------------------------------


def _is_json(content_type: str | None) -> bool:
    """Whether a request body should be parsed as JSON, from its Content-Type."""
    return content_type is not None and "application/json" in content_type.lower()


def _json_body(raw: bytes) -> dict[str, Any]:
    """Parse a JSON request body to a dict, tolerating anything malformed."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_int(query: dict[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default


def dispatch(  # noqa: PLR0911 -- a routing table has one return per route
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: dict[str, Any],
    *,
    dev: bool,
    write: bool = False,
    raw_body: bytes = b"",
    context_factory: ContextFactory = build_context,
) -> Response:
    """Route one request to a handler. Pure: no socket, no global state.

    ``dev`` unlocks the pipeline, graph and test routes; ``write`` unlocks
    adding source to the vault. A developer instance can also write, so callers
    pass ``write=write or dev``.

    Every handler opens its own :class:`AppContext` and closes it, because the
    SQLite connection inside must not cross threads. Building it is cheap and,
    crucially, re-arms the egress guard on the way in.
    """
    if method == "GET" and path == "/":
        return Response(status=200, raw=_index_html(), content_type="text/html; charset=utf-8")
    if path == "/favicon.ico":
        return Response(status=204, raw=b"", content_type="image/x-icon")
    if not path.startswith("/api/"):
        return _error("Not found.", 404)

    needs_graph = path in {"/api/status", "/api/neighbours"}
    dev_routes = {"/api/pipeline", "/api/neighbours", "/api/tests"}
    write_routes = {"/api/vault/entry", "/api/vault/ingest", "/api/vault/transcribe"}
    if path in dev_routes and not dev:
        return _error(
            "This action needs developer mode. Start the server with `diairy serve --dev`.", 403
        )
    if path in write_routes and not write:
        return _error(
            "Adding source is off. Start the server with `diairy serve --write` to enable it.", 403
        )

    try:
        with context_factory(with_graph=needs_graph) as ctx:
            if method == "GET":
                return _route_get(path, query, ctx=ctx, dev=dev, write=write)
            if method == "POST":
                return _route_post(path, body, query, raw_body, ctx=ctx)
            return _error("Not found.", 404)
    except DiairyError as exc:
        return _error(str(exc), 400)


def _route_get(  # noqa: PLR0911 -- a routing table has one return per route
    path: str,
    query: dict[str, list[str]],
    *,
    ctx: AppContext,
    dev: bool,
    write: bool,
) -> Response:
    if path == "/api/status":
        return Response(status=200, payload=_status_payload(ctx))
    if path == "/api/profile":
        return Response(status=200, payload=_profile_payload(ctx, dev=dev, write=write))
    if path == "/api/runs":
        return Response(status=200, payload=_runs_payload(ctx))
    if path == "/api/chunk":
        chunk_id = (query.get("id") or [""])[0]
        if not chunk_id:
            return _error("Pass a chunk id: /api/chunk?id=...", 400)
        return _chunk_payload(ctx, chunk_id)
    if path == "/api/neighbours":
        label = (query.get("label") or [""])[0]
        if not label:
            return _error("Pass a concept label: /api/neighbours?label=...", 400)
        return _neighbours_payload(ctx, label)
    return _error("Not found.", 404)


def _route_post(  # noqa: PLR0911 -- a routing table has one return per route
    path: str,
    body: dict[str, Any],
    query: dict[str, list[str]],
    raw_body: bytes,
    *,
    ctx: AppContext,
) -> Response:
    if path == "/api/search":
        text = str(body.get("query", "")).strip()
        if not text:
            return _error("Nothing to search for.", 400)
        return Response(status=200, payload=_search_payload(ctx, text, int(body.get("limit", 10))))
    if path == "/api/ask":
        question = str(body.get("question", "")).strip()
        if not question:
            return _error("Ask a question first.", 400)
        return Response(status=200, payload=_ask_payload(ctx, question, int(body.get("limit", 8))))
    if path == "/api/vault/entry":
        return _create_entry_payload(ctx, body)
    if path == "/api/vault/ingest":
        return _ingest_payload(ctx)
    if path == "/api/vault/transcribe":
        language = (query.get("language") or [""])[0].strip() or None
        suffix = (query.get("ext") or ["webm"])[0]
        return _transcribe_payload(ctx, raw_body, language=language, suffix=suffix)
    if path == "/api/pipeline":
        stage = str(body.get("stage", ""))
        raw_limit = body.get("limit")
        limit = int(raw_limit) if raw_limit not in (None, "") else None
        return _pipeline_payload(ctx, stage, limit)
    if path == "/api/tests":
        return _run_test_suite()
    return _error("Not found.", 404)


# -- socket shell -----------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Thin adapter: parse the request, call :func:`dispatch`, write the response."""

    dev: bool = False
    write: bool = False
    protocol_version = "HTTP/1.1"

    def _run(self, method: str) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        raw = self._read_raw() if method == "POST" else b""
        body = _json_body(raw) if _is_json(self.headers.get("Content-Type")) else {}
        response = dispatch(
            method, parts.path, query, body, dev=self.dev, write=self.write, raw_body=raw
        )
        self._write(response)

    def _read_raw(self) -> bytes:
        """Read the request body, capped so an oversized upload cannot exhaust memory."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        return self.rfile.read(min(length, _MAX_AUDIO_BYTES))

    def _write(self, response: Response) -> None:
        body = response.body()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._run("GET")

    def do_POST(self) -> None:
        self._run("POST")

    def log_message(self, *_args: Any) -> None:
        """Silence the default per-request logging; the terminal stays clean."""


def run_server(
    *, host: str = "127.0.0.1", port: int = 8765, dev: bool = False, write: bool = False
) -> None:
    """Arm the guard once, then serve until interrupted.

    A developer instance can do everything a writing one can, so ``--dev``
    implies write access.
    """
    settings: Settings = load_settings()
    settings.ensure_directories()
    install_egress_guard(allow_loopback=True, allowlist=[settings.ollama_address])

    handler = partial(_Handler)
    _Handler.dev = dev
    _Handler.write = write or dev
    httpd = ThreadingHTTPServer((host, port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover -- Ctrl-C is the way out
        pass
    finally:
        httpd.server_close()


def address(host: str, port: int) -> str:
    """The URL to print. ``0.0.0.0`` is not something a browser can open."""
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host  # noqa: S104
    return f"http://{display_host}:{port}"


if __name__ == "__main__":  # pragma: no cover
    run_server(dev="--dev" in sys.argv, write="--write" in sys.argv)
