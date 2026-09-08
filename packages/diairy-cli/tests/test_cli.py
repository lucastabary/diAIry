"""Command-line smoke tests.

They run the real commands against a temporary vault. Only the stages that need
no model are exercised here; extraction quality lives in ``evals/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from diairy.cli.main import app

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every diAIry path inside the temporary directory."""
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIAIRY_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("DIAIRY_VAULT_PATH", str(vault))
    monkeypatch.setenv("DIAIRY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DIAIRY_MODEL_PROFILE", "fake")
    yield vault


def test_init_creates_the_vault_and_the_store(cli_env: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert cli_env.is_dir()
    assert (cli_env / ".git").exists()


def test_status_on_an_empty_vault_reports_zeroes(cli_env: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "documents" in result.output
    assert "fake" in result.output


def test_ingest_then_search_finds_the_note(cli_env: Path) -> None:
    runner.invoke(app, ["init"])
    (cli_env / "2026-03-14.md").write_text(
        "# Lectures\n\nJ'ai fini Le Nom de la rose hier soir.\n", encoding="utf-8"
    )

    ingest = runner.invoke(app, ["ingest"])
    assert ingest.exit_code == 0, ingest.output
    assert "1 documents" in ingest.output

    found = runner.invoke(app, ["search", "Borges"])
    assert found.exit_code == 0
    assert "no matches" in found.output

    found = runner.invoke(app, ["search", "rose"])
    assert found.exit_code == 0
    assert "2026-03-14" in found.output


def test_ingest_is_idempotent(cli_env: Path) -> None:
    runner.invoke(app, ["init"])
    (cli_env / "note.md").write_text("Une idee.\n", encoding="utf-8")
    runner.invoke(app, ["ingest"])
    second = runner.invoke(app, ["ingest"])
    assert "0 new chunks" in second.output


def test_missing_vault_fails_with_a_useful_message(cli_env: Path) -> None:
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 1
    assert "diairy init" in result.output


def test_inspect_reports_an_unknown_chunk(cli_env: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["inspect", "does-not-exist"])
    assert result.exit_code == 1
    assert "no chunk" in result.output


def test_help_lists_the_pipeline_commands(cli_env: Path) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "ingest", "process", "run", "ask", "search", "status"):
        assert command in result.output
