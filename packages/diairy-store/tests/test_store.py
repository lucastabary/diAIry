"""The store: migrations, the append-only fact log, and hybrid search."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from diairy.core.models import (
    Chunk,
    ConceptRef,
    Document,
    DocumentVersion,
    Evidence,
    Fact,
    Run,
)
from diairy.store.db import connect
from diairy.store.migrations import CURRENT_VERSION, migrate
from diairy.store.repository import Repository
from diairy.store.search import SearchIndex, build_fts_query

NOW = datetime(2026, 3, 14, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> Repository:
    return Repository(connection)


def _seed_chunk(repository: Repository, text: str = "Relu Borges ce matin.") -> Chunk:
    repository.upsert_document(Document(id="d1", relative_path="a.md", first_seen_at=NOW))
    repository.insert_version(
        DocumentVersion(
            id="v1",
            document_id="d1",
            content_hash="h1",
            byte_size=len(text),
            ingested_at=NOW,
            event_time=NOW,
        )
    )
    chunk = Chunk(
        id="c1", document_version_id="v1", ordinal=0, char_start=0, char_end=len(text), text=text
    )
    repository.insert_chunks([chunk])
    return chunk


def _seed_run(repository: Repository, run_id: str = "r1") -> str:
    repository.start_run(Run(id=run_id, started_at=NOW))
    return run_id


def _fact(chunk: Chunk, run_id: str, fact_id: str = "f1", predicate: str = "a relu") -> Fact:
    return Fact(
        id=fact_id,
        subject=ConceptRef(label="moi", type="personne"),
        predicate=predicate,
        object=ConceptRef(label="Borges", type="auteur"),
        confidence=0.9,
        evidence=Evidence(chunk_id=chunk.id, char_start=0, char_end=5, quote="Relu"),
        run_id=run_id,
        event_time=NOW,
        knowledge_time=NOW,
    )


def test_migrations_are_applied_once(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    assert migrate(conn) == []  # connect() already applied them
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    assert row["v"] == CURRENT_VERSION
    conn.close()


def test_reopening_a_database_does_not_reapply_migrations(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    connect(path).close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == (
        CURRENT_VERSION
    )
    conn.close()


def test_inserting_a_document_twice_is_a_no_op(repository: Repository) -> None:
    _seed_chunk(repository)
    _seed_chunk(repository)
    assert repository.document_count() == 1
    assert repository.chunk_count() == 1


def test_facts_round_trip_with_their_provenance(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    run_id = _seed_run(repository)
    assert repository.insert_facts([_fact(chunk, run_id)]) == 1

    (stored,) = repository.facts_for_chunk(chunk.id)
    assert stored.predicate == "a relu"
    assert stored.evidence.quote == "Relu"
    assert stored.is_current


def test_superseding_marks_facts_without_deleting_them(repository: Repository) -> None:
    """The no-deletion invariant, asserted directly."""
    chunk = _seed_chunk(repository)
    run_id = _seed_run(repository)
    repository.insert_facts([_fact(chunk, run_id)])
    second_run = _seed_run(repository, "r2")

    assert repository.supersede_facts_of_version("v1", second_run) == 1

    assert repository.fact_count() == 0  # nothing is current any more
    assert repository.fact_count(current_only=False) == 1  # but the row is still there

    (stored,) = repository.facts_for_chunk(chunk.id, current_only=False)
    assert stored.superseded_by == second_run
    assert not stored.is_current


def test_superseding_twice_does_not_rewrite_history(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    repository.insert_facts([_fact(chunk, _seed_run(repository))])
    repository.supersede_facts_of_version("v1", _seed_run(repository, "r2"))
    assert repository.supersede_facts_of_version("v1", _seed_run(repository, "r3")) == 0
    (stored,) = repository.facts_for_chunk(chunk.id, current_only=False)
    assert stored.superseded_by == "r2"


def test_pending_chunks_carry_the_event_time_and_drain(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    pending = repository.chunks_without_facts()
    assert len(pending) == 1
    assert pending[0].event_time == NOW

    repository.insert_facts([_fact(chunk, _seed_run(repository))])
    assert repository.chunks_without_facts() == []


def test_run_lifecycle_is_recorded(repository: Repository) -> None:
    run_id = _seed_run(repository)
    repository.finish_run(run_id, status="completed", stats={"facts_written": 3})
    run = repository.get_run(run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.stats["facts_written"] == 3
    assert run.finished_at is not None
    assert repository.recent_runs()[0].id == run_id


def test_rejections_are_recorded_and_counted(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    run_id = _seed_run(repository)
    repository.record_rejections(
        run_id, chunk.id, [("quote_not_found", "x"), ("quote_not_found", "y")]
    )
    assert repository.rejection_counts(run_id) == {"quote_not_found": 2}


def test_vocabulary_reports_the_most_used_predicates(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    run_id = _seed_run(repository)
    repository.insert_facts(
        [
            _fact(chunk, run_id, "f1", "a relu"),
            _fact(chunk, run_id, "f2", "a aime"),
        ]
    )
    _, predicates = repository.vocabulary()
    assert set(predicates) == {"a relu", "a aime"}


def test_concepts_accumulate_occurrences(repository: Repository) -> None:
    from diairy.core.models import CanonicalConcept

    concept = CanonicalConcept(id="k1", label="borges", type="auteur")
    repository.bump_concept(concept)
    repository.bump_concept(concept)
    (stored,) = repository.known_concepts()
    assert stored.occurrence_count == 2
    assert repository.concept_count() == 1


def test_full_text_search_is_accent_insensitive(repository: Repository) -> None:
    _seed_chunk(repository, "La bibliothèque de Babel me hante encore.")
    index = SearchIndex(repository.connection)
    assert index.lexical("bibliotheque") == ["c1"]
    assert index.lexical("BIBLIOTHÈQUE") == ["c1"]


def test_hybrid_search_works_without_any_vectors(repository: Repository) -> None:
    _seed_chunk(repository, "Relu Borges ce matin.")
    hits = SearchIndex(repository.connection).hybrid("Borges")
    assert [hit.chunk_id for hit in hits] == ["c1"]
    assert hits[0].semantic_rank is None
    assert not hits[0].found_by_both


def test_search_returns_nothing_rather_than_failing_on_odd_input(
    repository: Repository,
) -> None:
    _seed_chunk(repository)
    index = SearchIndex(repository.connection)
    assert index.lexical("") == []
    assert index.hybrid("???") == []


@pytest.mark.parametrize(
    "dangerous",
    ['borges" OR "', "NEAR(", "a AND NOT b", "*", "'; DROP TABLE chunks; --"],
)
def test_user_input_cannot_become_fts_syntax(repository: Repository, dangerous: str) -> None:
    _seed_chunk(repository)
    SearchIndex(repository.connection).lexical(dangerous)  # must not raise
    assert repository.chunk_count() == 1


def test_build_fts_query_quotes_every_token() -> None:
    assert build_fts_query("le nom") == '"le" OR "nom"'
    assert build_fts_query("  ") == ""


def test_chunk_sources_supply_citation_metadata(repository: Repository) -> None:
    chunk = _seed_chunk(repository)
    sources = repository.chunk_sources([chunk.id])
    assert sources[chunk.id][0] == "a.md"


def test_vector_index_reports_dimension_conflicts(repository: Repository) -> None:
    from diairy.core.errors import StoreError

    index = SearchIndex(repository.connection)
    if not index.ensure_vector_table(64, "hashing"):
        pytest.skip("sqlite-vec is not loadable in this interpreter")
    with pytest.raises(StoreError, match="rebuild"):
        index.ensure_vector_table(1024, "bge-m3")


def test_vector_round_trip(repository: Repository) -> None:
    _seed_chunk(repository)
    index = SearchIndex(repository.connection)
    if not index.ensure_vector_table(4, "test"):
        pytest.skip("sqlite-vec is not loadable in this interpreter")
    assert index.unindexed_chunk_ids() == ["c1"]
    assert index.index_embeddings([("c1", [1.0, 0.0, 0.0, 0.0])]) == 1
    assert index.indexed_vector_count() == 1
    assert index.unindexed_chunk_ids() == []
    assert index.semantic([1.0, 0.0, 0.0, 0.0], limit=3) == ["c1"]
