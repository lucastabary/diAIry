"""Identifiers must be stable across machines, and config must obey precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from diairy.core.config import Settings, load_settings
from diairy.core.errors import ConfigError
from diairy.core.hashing import digest_of, sha256_text
from diairy.core.ids import (
    chunk_id,
    concept_id,
    document_id,
    document_version_id,
    fact_id,
    new_run_id,
    normalize_relative_path,
)


def test_document_id_is_path_separator_agnostic() -> None:
    """The same note must get the same id on Windows and on macOS."""
    assert document_id("notes\\2026\\09.md") == document_id("notes/2026/09.md")


def test_document_id_is_stable() -> None:
    assert document_id("a.md") == document_id("a.md")
    assert document_id("a.md") != document_id("b.md")


def test_normalize_relative_path_strips_separators() -> None:
    assert normalize_relative_path("/notes/a.md/") == "notes/a.md"


def test_digest_parts_cannot_collide_by_concatenation() -> None:
    assert digest_of(["ab", "c"]) != digest_of(["a", "bc"])


def test_sha256_text_is_deterministic() -> None:
    assert sha256_text("bonjour") == sha256_text("bonjour")


def test_derived_ids_depend_on_every_input() -> None:
    version = document_version_id("doc", "hash")
    assert version != document_version_id("doc", "other")
    assert chunk_id(version, 0, 10) != chunk_id(version, 0, 11)
    assert concept_id("borges", "auteur") != concept_id("borges", "livre")


def test_fact_id_separates_identical_claims_from_different_evidence() -> None:
    """Two identical claims backed by two different sentences are two facts."""
    first = fact_id(chunk="c", subject="s", predicate="p", obj="o", char_start=0, char_end=5)
    second = fact_id(chunk="c", subject="s", predicate="p", obj="o", char_start=20, char_end=25)
    assert first != second


def test_run_ids_are_unique() -> None:
    assert new_run_id() != new_run_id()


def test_settings_derive_paths_from_the_data_directory(tmp_path: Path) -> None:
    settings = Settings(vault_path=tmp_path / "v", data_dir=tmp_path / "d")
    assert settings.db_path == tmp_path / "d" / "diairy.db"
    assert settings.graph_path == tmp_path / "d" / "graph"
    assert settings.runs_dir == tmp_path / "d" / "runs"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_address == ("127.0.0.1", 11434)


def test_ensure_directories_never_creates_the_vault(tmp_path: Path) -> None:
    """Creating the vault is an explicit act (`diairy init`), never a side effect."""
    settings = Settings(vault_path=tmp_path / "vault", data_dir=tmp_path / "data")
    settings.ensure_directories()
    assert settings.data_dir.is_dir()
    assert settings.runs_dir.is_dir()
    assert not settings.vault_path.exists()


def test_environment_outranks_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model_profile = "small"\nlog_level = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("DIAIRY_MODEL_PROFILE", "large")

    settings = load_settings(config)

    assert settings.model_profile == "large"  # environment wins
    assert settings.log_level == "DEBUG"  # file still supplies what the env does not


def test_explicit_overrides_outrank_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model_profile = "small"\n', encoding="utf-8")
    monkeypatch.setenv("DIAIRY_MODEL_PROFILE", "large")
    assert load_settings(config, model_profile="fake").model_profile == "fake"


def test_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.toml").model_profile == "medium"


def test_invalid_config_file_is_reported_clearly(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("this is not = = toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_settings(config)
