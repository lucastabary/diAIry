"""End-to-end pipeline tests, with deterministic stand-ins for every model.

These are the tests that would catch a regression in the parts users actually
feel: re-running does nothing, editing a note supersedes without losing, and a
fact can always be traced back to a file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from diairy.core.config import Settings
from diairy.core.ids import document_id
from diairy.nlp.canonical import Canonicalizer
from diairy.nlp.extract import FactExtractor
from diairy.nlp.fake import HashingEmbeddings
from diairy.nlp.provider import LLMResponse
from diairy.pipeline.runlog import read_run_log
from diairy.pipeline.runner import Pipeline
from diairy.store.db import connect
from diairy.store.repository import Repository
from diairy.store.search import SearchIndex

NOTE = """---
date: 2026-03-14
---

# Lectures

J'ai fini Le Nom de la rose hier soir.
"""

EDITED_NOTE = """---
date: 2026-03-14
---

# Lectures

J'ai abandonne Le Nom de la rose au tiers.
"""


class QuotingLLM:
    """Returns one fact quoting a phrase it knows is present in the source.

    Deliberately dumb: the point is to exercise the real validation, storage and
    canonicalisation paths, not to simulate intelligence.
    """

    def __init__(self, quote: str, obj: str = "Le Nom de la rose") -> None:
        self.quote = quote
        self.obj = obj
        self.calls = 0

    @property
    def name(self) -> str:
        return "quoting"

    @property
    def model(self) -> str:
        return "quoting-stub"

    def complete(self, *, system: str, prompt: str, **_: object) -> LLMResponse:  # noqa: ARG002
        self.calls += 1
        payload = {
            "facts": [
                {
                    "subject": {"label": "moi", "type": "personne"},
                    "predicate": "a lu",
                    "object": {"label": self.obj, "type": "livre"},
                    "object_is_literal": False,
                    "confidence": 0.9,
                    "quote": self.quote,
                }
            ]
            if self.quote in prompt
            else []
        }
        return LLMResponse(text=json.dumps(payload), model=self.model)


@pytest.fixture
def pipeline_parts(settings: Settings) -> Iterator[tuple[Settings, Repository, SearchIndex]]:
    settings.ensure_directories()
    connection = connect(settings.db_path)
    yield settings, Repository(connection), SearchIndex(connection)
    connection.close()


def _build(
    settings: Settings,
    repository: Repository,
    search: SearchIndex,
    llm: QuotingLLM,
) -> Pipeline:
    embedder = HashingEmbeddings(dimensions=32)
    return Pipeline(
        settings=settings,
        repository=repository,
        search=search,
        extractor=FactExtractor(llm),
        embedder=embedder,
        canonicalizer=Canonicalizer(threshold=0.9, embedder=embedder),
    )


def _write_note(settings: Settings, content: str, name: str = "2026-03-14.md") -> Path:
    path = settings.vault_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_ingest_records_documents_chunks_and_a_git_snapshot(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)

    stats = _build(settings, repository, search, QuotingLLM("hier soir")).ingest()

    assert stats.documents_seen == 1
    assert stats.documents_new == 1
    assert stats.chunks_new >= 1
    assert stats.git_commit is not None
    assert repository.chunk_count() >= 1


def test_re_ingesting_an_unchanged_vault_does_nothing(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))

    pipeline.ingest()
    chunks_after_first = repository.chunk_count()
    second = pipeline.ingest()

    assert second.versions_new == 0
    assert second.chunks_new == 0
    assert repository.chunk_count() == chunks_after_first


def test_process_writes_facts_with_traceable_provenance(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()

    stats = pipeline.process()

    assert stats.facts_written >= 1
    assert stats.facts_rejected == 0

    version = repository.latest_version(document_id("2026-03-14.md"))
    assert version is not None
    chunks = repository.chunks_for_version(version.id)
    facts = [fact for chunk in chunks for fact in repository.facts_for_chunk(chunk.id)]
    assert facts
    fact = facts[0]
    assert fact.evidence.quote == "hier soir"
    assert fact.event_time.date().isoformat() == "2026-03-14"

    # The quote must slice back out of the stored chunk.
    chunk = repository.get_chunk(fact.evidence.chunk_id)
    assert chunk is not None
    relative_start = fact.evidence.char_start - chunk.char_start
    relative_end = fact.evidence.char_end - chunk.char_start
    assert chunk.text[relative_start:relative_end] == "hier soir"


def test_processing_twice_does_not_duplicate_facts(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()
    pipeline.process()
    before = repository.fact_count()

    assert pipeline.process().chunks_processed == 0
    assert repository.fact_count() == before


def test_editing_a_note_supersedes_old_facts_without_deleting_them(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()
    pipeline.process()
    original_total = repository.fact_count(current_only=False)
    assert original_total >= 1

    _write_note(settings, EDITED_NOTE)
    ingested = pipeline.ingest()

    assert ingested.versions_new == 1
    assert ingested.facts_superseded == original_total
    assert repository.fact_count() == 0  # nothing current until the new text is processed
    assert repository.fact_count(current_only=False) == original_total  # nothing lost


def test_canonical_concepts_are_created_and_counted(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()
    pipeline.process()

    labels = {concept.label for concept in repository.known_concepts()}
    assert "moi" in labels
    assert "le nom de la rose" in labels


def test_the_run_log_records_the_prompt_and_the_raw_response(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()
    stats = pipeline.process()

    log_path = settings.runs_dir / f"{stats.run_id}.jsonl.gz"
    assert log_path.exists()
    entries = list(read_run_log(log_path))
    assert entries
    assert entries[0]["kind"] == "extraction"
    assert "Le Nom de la rose" in entries[0]["prompt"]
    assert "hier soir" in entries[0]["response"]


def test_rejections_are_recorded_when_the_model_invents(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    # This quote appears in the prompt (as the model's own claim) but not in the
    # source text, so validation must reject it.
    pipeline = _build(settings, repository, search, QuotingLLM("Lectures", obj="Guerre et Paix"))
    pipeline.ingest()
    stats = pipeline.process()

    assert stats.facts_written == 0
    assert stats.rejection_reasons.get("quote_not_found") == 1


def test_embedding_indexes_every_chunk_once(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.ingest()

    first = pipeline.embed()
    if not first.available:
        pytest.skip(first.note)
    assert first.chunks_embedded == repository.chunk_count()
    assert pipeline.embed().chunks_embedded == 0  # nothing left to do


def test_process_without_an_extractor_is_refused(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    pipeline = Pipeline(settings=settings, repository=repository, search=search)
    pipeline.ingest()
    with pytest.raises(ValueError, match="extractor"):
        pipeline.process()


def test_graph_projection_mirrors_the_current_facts(
    pipeline_parts: tuple[Settings, Repository, SearchIndex],
) -> None:
    pytest.importorskip("kuzu")
    from diairy.store.graph import GraphProjection

    settings, repository, search = pipeline_parts
    _write_note(settings, NOTE)
    graph = GraphProjection(settings.graph_path)
    pipeline = _build(settings, repository, search, QuotingLLM("hier soir"))
    pipeline.graph = graph
    pipeline.ingest()
    pipeline.process()

    stats = pipeline.project(rebuild=True)
    assert stats.concepts >= 2
    assert stats.edges >= 1
    assert graph.concept_count() == stats.concepts
    assert graph.edge_count() == stats.edges

    # Projecting again must not duplicate anything.
    pipeline.project()
    assert graph.edge_count() == stats.edges
    graph.close()
