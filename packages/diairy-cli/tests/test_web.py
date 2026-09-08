"""Web UI API tests.

They drive :func:`dispatch` directly -- no socket is ever opened -- so the whole
API is exercised while the autouse network block stays in force. The profile is
``fake``, so no model is called either. This is the same contract the CLI tests
hold: only the model-free paths are asserted on here; extraction quality is an
eval, not a test.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from diairy.cli.web import Response, _Handler, address, dispatch


@pytest.fixture
def web_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every diAIry path inside the temporary directory, on the fake profile."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIAIRY_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("DIAIRY_VAULT_PATH", str(vault))
    monkeypatch.setenv("DIAIRY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DIAIRY_MODEL_PROFILE", "fake")
    yield vault


def _get(path: str, *, dev: bool = False, **query: str) -> Response:
    return dispatch("GET", path, {k: [v] for k, v in query.items()}, {}, dev=dev)


def _post(
    path: str, body: dict[str, object], *, dev: bool = False, write: bool = False
) -> Response:
    return dispatch("POST", path, {}, body, dev=dev, write=write)


def test_index_is_served_as_html(web_env: Path) -> None:
    response = dispatch("GET", "/", {}, {}, dev=False)
    assert response.status == 200
    assert response.content_type.startswith("text/html")
    assert b"diAIry" in response.body()


def test_status_on_an_empty_store_reports_zeroes(web_env: Path) -> None:
    response = _get("/api/status")
    assert response.status == 200
    assert response.payload["documents"] == 0
    assert response.payload["profile"] == "fake"


def test_pipeline_is_refused_without_developer_mode(web_env: Path) -> None:
    response = _post("/api/pipeline", {"stage": "ingest"}, dev=False)
    assert response.status == 403
    assert "developer mode" in response.payload["error"]


def test_ingest_then_search_finds_the_note(web_env: Path) -> None:
    web_env.mkdir()
    (web_env / "2026-03-14.md").write_text(
        "# Lectures\n\nJ'ai fini Le Nom de la rose hier soir.\n", encoding="utf-8"
    )

    ingested = _post("/api/pipeline", {"stage": "ingest"}, dev=True)
    assert ingested.status == 200
    assert ingested.payload["stats"]["chunks_new"] >= 1

    found = _post("/api/search", {"query": "rose", "limit": 5})
    assert found.status == 200
    passages = found.payload["passages"]
    assert passages, "the note should be retrievable by full-text search"
    assert passages[0]["source_path"].endswith("2026-03-14.md")


def test_search_rejects_an_empty_query(web_env: Path) -> None:
    response = _post("/api/search", {"query": "   "})
    assert response.status == 400


def test_inspect_reports_an_unknown_chunk(web_env: Path) -> None:
    response = _get("/api/chunk", id="does-not-exist")
    assert response.status == 404
    assert "chunk" in response.payload["error"].lower()


def test_inspect_returns_the_chunk_after_ingest(web_env: Path) -> None:
    web_env.mkdir()
    (web_env / "note.md").write_text("Une idee simple mais tenace.\n", encoding="utf-8")
    _post("/api/pipeline", {"stage": "ingest"}, dev=True)

    hits = _post("/api/search", {"query": "idee"})
    chunk_id = hits.payload["passages"][0]["chunk_id"]

    response = _get("/api/chunk", id=chunk_id)
    assert response.status == 200
    assert "idee" in response.payload["chunk"]["text"]
    assert isinstance(response.payload["facts"], list)


def test_profile_reports_developer_flag(web_env: Path) -> None:
    assert _get("/api/profile", dev=True).payload["dev"] is True
    assert _get("/api/profile", dev=False).payload["dev"] is False


def test_adding_source_is_refused_without_write_mode(web_env: Path) -> None:
    response = _post("/api/vault/entry", {"body": "Une note."}, write=False)
    assert response.status == 403
    assert "serve --write" in response.payload["error"]
    assert not web_env.exists(), "a refused write must not touch the vault"


def test_adding_source_writes_a_file_the_pipeline_can_read(web_env: Path) -> None:
    created = _post(
        "/api/vault/entry",
        {"title": "Voyage", "date": "2026-04-02", "body": "J'ai adore Lisbonne, les tramways."},
        write=True,
    )
    assert created.status == 201
    assert created.payload["relative_path"] == "2026-04-02-voyage.md"
    assert (web_env / "2026-04-02-voyage.md").exists()

    ingested = _post("/api/vault/ingest", {}, write=True)
    assert ingested.status == 200
    assert ingested.payload["stats"]["chunks_new"] >= 1

    found = _post("/api/search", {"query": "tramways"})
    assert found.payload["passages"], "the freshly written note should be searchable"
    assert found.payload["passages"][0]["source_path"] == "2026-04-02-voyage.md"


def test_an_empty_entry_is_refused(web_env: Path) -> None:
    response = _post("/api/vault/entry", {"body": "   "}, write=True)
    assert response.status == 400


def test_a_bad_date_is_reported(web_env: Path) -> None:
    response = _post("/api/vault/entry", {"body": "Note.", "date": "not-a-date"}, write=True)
    assert response.status == 400
    assert "YYYY-MM-DD" in response.payload["error"]


def test_developer_mode_can_also_write(web_env: Path) -> None:
    # run_server sets write=write or dev; dispatch is told the resolved value.
    created = _post("/api/vault/entry", {"body": "Note dev."}, dev=True, write=True)
    assert created.status == 201


def test_unknown_api_route_is_a_404(web_env: Path) -> None:
    assert _get("/api/nope").status == 404


# -- developer routes -------------------------------------------------------


@pytest.mark.parametrize("stage", ["process", "embed", "project"])
def test_each_pipeline_stage_runs_on_an_empty_store(web_env: Path, stage: str) -> None:
    response = _post("/api/pipeline", {"stage": stage}, dev=True)
    assert response.status == 200
    assert response.payload["stage"] == stage
    assert "stats" in response.payload


def test_an_unknown_pipeline_stage_is_reported(web_env: Path) -> None:
    response = _post("/api/pipeline", {"stage": "nope"}, dev=True)
    assert response.status == 400
    assert "Unknown stage" in response.payload["error"]


def test_runs_are_listed(web_env: Path) -> None:
    response = _get("/api/runs")
    assert response.status == 200
    assert response.payload["runs"] == []


def test_a_chunk_request_without_an_id_is_rejected(web_env: Path) -> None:
    response = _get("/api/chunk")
    assert response.status == 400
    assert "chunk id" in response.payload["error"]


def test_neighbours_without_a_label_is_rejected(web_env: Path) -> None:
    response = _get("/api/neighbours", dev=True)
    assert response.status == 400
    assert "concept label" in response.payload["error"]


def test_asking_without_a_question_is_rejected(web_env: Path) -> None:
    response = _post("/api/ask", {"question": "   "})
    assert response.status == 400


def test_the_test_route_reports_the_suite_result(
    web_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The route shells out to pytest; here we stub the subprocess so the test
    # asserts the wiring, not a nested run of the whole suite.
    class _Completed:
        returncode = 0
        stdout = "42 passed"
        stderr = ""

    def _fake_run(*_a: object, **_k: object) -> _Completed:
        return _Completed()

    monkeypatch.setattr("diairy.cli.web.subprocess.run", _fake_run)
    response = _post("/api/tests", {}, dev=True)
    assert response.status == 200
    assert response.payload["passed"] is True
    assert "passed" in response.payload["output"]


def test_the_test_route_reports_a_missing_uv(
    web_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("diairy.cli.web.subprocess.run", _boom)
    response = _post("/api/tests", {}, dev=True)
    assert response.status == 500
    assert "uv" in response.payload["error"]


# -- helpers and the socket shell -------------------------------------------


def test_address_makes_a_wildcard_bind_openable() -> None:
    assert address("0.0.0.0", 8765) == "http://127.0.0.1:8765"  # noqa: S104
    assert address("::", 80) == "http://127.0.0.1:80"
    assert address("127.0.0.1", 9000) == "http://127.0.0.1:9000"


@pytest.mark.loopback
def test_the_socket_shell_serves_requests_over_loopback(web_env: Path) -> None:
    # Exercise the thin _Handler adapter for real over loopback: this is the one
    # test that opens a socket, so run_server's glue is validated end to end.
    _Handler.dev = True
    _Handler.write = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler))
    port = httpd.server_address[1]
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/")
        page = conn.getresponse()
        assert page.status == 200
        assert b"diAIry" in page.read()

        payload = json.dumps({"query": "rien"})
        conn.request(
            "POST", "/api/search", body=payload, headers={"Content-Type": "application/json"}
        )
        searched = conn.getresponse()
        body = searched.read()
        assert searched.status == 200
        assert b"passages" in body
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_thread.join(timeout=5)
