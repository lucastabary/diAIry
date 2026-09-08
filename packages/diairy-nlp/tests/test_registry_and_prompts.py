"""The model pool and the versioned prompts."""

from __future__ import annotations

from pathlib import Path

import pytest

from diairy.core.config import Settings
from diairy.core.errors import ConfigError
from diairy.nlp.factory import build_embedder, build_llm, resolve_profile
from diairy.nlp.prompts import load_prompt
from diairy.nlp.registry import load_registry
from diairy.nlp.schema import extraction_json_schema


def test_every_profile_declares_extraction_and_embedding() -> None:
    registry = load_registry()
    assert registry.profiles
    for name, profile in registry.profiles.items():
        assert profile.extraction.model, f"{name} has no extraction model"
        assert profile.embedding.model, f"{name} has no embedding model"


def test_every_ollama_embedding_declares_its_dimensions() -> None:
    """A missing dimension would silently corrupt the vector index."""
    for name, profile in load_registry().profiles.items():
        if profile.embedding.backend == "ollama":
            assert profile.embedding.dimensions, f"{name} embedding has no dimensions"


def test_the_default_profile_exists() -> None:
    registry = load_registry()
    assert registry.default_profile in registry.profiles


def test_unknown_profile_lists_the_available_ones() -> None:
    with pytest.raises(ConfigError, match="Available profiles"):
        load_registry().profile("gpu-cluster")


def test_a_custom_registry_file_can_be_loaded(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        "version = 1\n"
        'default_profile = "tiny"\n'
        "[profiles.tiny]\n"
        "[profiles.tiny.extraction]\n"
        'backend = "fake"\n'
        'model = "scripted"\n'
        "[profiles.tiny.embedding]\n"
        'backend = "fake"\n'
        'model = "hashing"\n'
        "dimensions = 8\n",
        encoding="utf-8",
    )
    assert load_registry(path).profile("tiny").embedding.dimensions == 8


def test_the_fake_profile_builds_providers_without_any_network(tmp_path: Path) -> None:
    settings = Settings(vault_path=tmp_path, data_dir=tmp_path, model_profile="fake")
    profile = resolve_profile(settings)
    llm = build_llm(profile.extraction, settings)
    embedder = build_embedder(profile.embedding, settings)
    assert llm.name == "scripted"
    assert embedder.dimensions == 64
    assert len(embedder.embed(["bonjour"])[0]) == 64


def test_an_unknown_backend_is_refused_clearly(tmp_path: Path) -> None:
    from diairy.nlp.registry import ModelSpec

    settings = Settings(vault_path=tmp_path, data_dir=tmp_path)
    with pytest.raises(ConfigError, match="Unknown LLM backend"):
        build_llm(ModelSpec(backend="mlx", model="whatever"), settings)


def test_prompt_version_is_derived_from_content() -> None:
    prompt = load_prompt("extraction")
    assert prompt.version
    assert prompt.system
    assert load_prompt("extraction").version == prompt.version


def test_prompts_render_with_braces_in_the_content() -> None:
    """Prompts contain JSON, so the template engine must not treat { } specially."""
    rendered = load_prompt("extraction").render(
        vocabulary="{}",
        signals="{'a': 1}",
        heading_path="x",
        event_date="2026-01-01",
        text="{not a placeholder}",
    )
    assert "{not a placeholder}" in rendered


def test_extraction_schema_requires_a_quote() -> None:
    schema = extraction_json_schema()
    fact = schema["$defs"]["RawFact"]
    assert "quote" in fact["required"]
    assert "subject" in fact["required"]
