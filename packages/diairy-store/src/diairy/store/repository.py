"""Reading and writing the fact log.

This is the only module that writes SQL against the fact tables, and it is where
the no-deletion rule is enforced in practice: there is no method here that
removes a fact. When a document changes, the facts drawn from its previous
version are marked superseded by the run that noticed, and they stay in the
table forever.

That is what makes "what did I think of this book when I read it" answerable
years later, even after the entry itself has been rewritten.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from diairy.core.clock import from_iso, to_iso, utc_now
from diairy.core.models import (
    Alias,
    CanonicalConcept,
    Chunk,
    ConceptRef,
    Document,
    DocumentVersion,
    Evidence,
    Fact,
    Run,
    RunStatus,
)

DEFAULT_VOCABULARY_LIMIT = 60


@dataclass(frozen=True)
class PendingChunk:
    """A chunk awaiting extraction, with the event time it will pass on."""

    chunk: Chunk
    event_time: datetime


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"],
        document_version_id=row["document_version_id"],
        ordinal=row["ordinal"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        text=row["text"],
        heading_path=tuple(json.loads(row["heading_path"] or "[]")),
    )


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        subject=ConceptRef(label=row["subject_label"], type=row["subject_type"]),
        predicate=row["predicate"],
        object=ConceptRef(label=row["object_label"], type=row["object_type"]),
        object_is_literal=bool(row["object_is_literal"]),
        confidence=row["confidence"],
        evidence=Evidence(
            chunk_id=row["chunk_id"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            quote=row["quote"],
        ),
        run_id=row["run_id"],
        event_time=from_iso(row["event_time"]),
        knowledge_time=from_iso(row["knowledge_time"]),
        superseded_by=row["superseded_by"],
    )


class Repository:
    """Data access for documents, chunks, runs and facts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # -- meta ---------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- documents ----------------------------------------------------------

    def upsert_document(self, document: Document) -> None:
        self.connection.execute(
            "INSERT INTO documents (id, relative_path, first_seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (document.id, document.relative_path, to_iso(document.first_seen_at)),
        )

    def latest_version(self, document_id: str) -> DocumentVersion | None:
        """Return the most recently ingested version of a document."""
        row = self.connection.execute(
            "SELECT * FROM document_versions WHERE document_id = ? "
            "ORDER BY ingested_at DESC, id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return DocumentVersion(
            id=row["id"],
            document_id=row["document_id"],
            content_hash=row["content_hash"],
            byte_size=row["byte_size"],
            ingested_at=from_iso(row["ingested_at"]),
            event_time=from_iso(row["event_time"]),
            git_commit=row["git_commit"],
        )

    def insert_version(self, version: DocumentVersion) -> None:
        self.connection.execute(
            "INSERT INTO document_versions "
            "(id, document_id, content_hash, byte_size, ingested_at, event_time, git_commit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (
                version.id,
                version.document_id,
                version.content_hash,
                version.byte_size,
                to_iso(version.ingested_at),
                to_iso(version.event_time),
                version.git_commit,
            ),
        )

    def document_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"])

    # -- chunks -------------------------------------------------------------

    def insert_chunks(self, chunks: Sequence[Chunk]) -> int:
        """Insert chunks and mirror their text into the full-text index."""
        inserted = 0
        for chunk in chunks:
            cursor = self.connection.execute(
                "INSERT INTO chunks "
                "(id, document_version_id, ordinal, char_start, char_end, text, heading_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (
                    chunk.id,
                    chunk.document_version_id,
                    chunk.ordinal,
                    chunk.char_start,
                    chunk.char_end,
                    chunk.text,
                    json.dumps(list(chunk.heading_path), ensure_ascii=False),
                ),
            )
            if cursor.rowcount:
                inserted += 1
                self.connection.execute(
                    "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
                    (chunk.id, chunk.text),
                )
        return inserted

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self.connection.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return None if row is None else _row_to_chunk(row)

    def chunks_for_version(self, version_id: str) -> list[Chunk]:
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE document_version_id = ? ORDER BY ordinal",
            (version_id,),
        ).fetchall()
        return [_row_to_chunk(row) for row in rows]

    def chunk_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return int(row["n"])

    def chunks_without_facts(self, limit: int | None = None) -> list[PendingChunk]:
        """Chunks no run has extracted from yet. The pipeline's work queue.

        Carries the document's event time along, because a fact inherits when
        the entry was *written*, not when the batch happened to run.
        """
        query = (
            "SELECT c.*, v.event_time AS version_event_time FROM chunks c "
            "JOIN document_versions v ON v.id = c.document_version_id "
            "WHERE NOT EXISTS (SELECT 1 FROM facts f WHERE f.chunk_id = c.id) "
            "ORDER BY v.event_time, c.document_version_id, c.ordinal"
        )
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.connection.execute(query, params).fetchall()
        return [
            PendingChunk(
                chunk=_row_to_chunk(row),
                event_time=from_iso(row["version_event_time"]),
            )
            for row in rows
        ]

    def chunk_texts(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """Fetch chunk texts in bulk, for the embedding stage."""
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.connection.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({placeholders})",  # noqa: S608
            tuple(chunk_ids),
        ).fetchall()
        return {row["id"]: row["text"] for row in rows}

    # -- runs ---------------------------------------------------------------

    def start_run(self, run: Run) -> None:
        self.connection.execute(
            "INSERT INTO runs (id, started_at, status, model, profile, prompt_version, "
            "pipeline_version, stats) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                to_iso(run.started_at),
                run.status,
                run.model,
                run.profile,
                run.prompt_version,
                run.pipeline_version,
                json.dumps(run.stats),
            ),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        stats: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            "UPDATE runs SET finished_at = ?, status = ?, stats = ?, error = ? WHERE id = ?",
            (to_iso(utc_now()), status, json.dumps(stats or {}), error, run_id),
        )

    def get_run(self, run_id: str) -> Run | None:
        row = self.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return Run(
            id=row["id"],
            started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]) if row["finished_at"] else None,
            status=row["status"],
            model=row["model"],
            profile=row["profile"],
            prompt_version=row["prompt_version"],
            pipeline_version=row["pipeline_version"],
            stats=json.loads(row["stats"]),
            error=row["error"],
        )

    def recent_runs(self, limit: int = 10) -> list[Run]:
        rows = self.connection.execute(
            "SELECT id FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        runs = [self.get_run(row["id"]) for row in rows]
        return [run for run in runs if run is not None]

    # -- facts --------------------------------------------------------------

    def insert_facts(self, facts: Iterable[Fact]) -> int:
        inserted = 0
        for fact in facts:
            cursor = self.connection.execute(
                "INSERT INTO facts (id, subject_label, subject_type, predicate, "
                "object_label, object_type, object_is_literal, confidence, chunk_id, "
                "char_start, char_end, quote, run_id, event_time, knowledge_time, "
                "superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    fact.id,
                    fact.subject.label,
                    fact.subject.type,
                    fact.predicate,
                    fact.object.label,
                    fact.object.type,
                    int(fact.object_is_literal),
                    fact.confidence,
                    fact.evidence.chunk_id,
                    fact.evidence.char_start,
                    fact.evidence.char_end,
                    fact.evidence.quote,
                    fact.run_id,
                    to_iso(fact.event_time),
                    to_iso(fact.knowledge_time),
                    fact.superseded_by,
                ),
            )
            inserted += cursor.rowcount or 0
        return inserted

    def supersede_facts_of_version(self, version_id: str, run_id: str) -> int:
        """Mark the facts of a superseded document version as no longer current.

        Nothing is deleted. The rows stay, and time-travel queries still see
        them as the truth of their era.
        """
        cursor = self.connection.execute(
            "UPDATE facts SET superseded_by = ? WHERE superseded_by IS NULL AND chunk_id IN "
            "(SELECT id FROM chunks WHERE document_version_id = ?)",
            (run_id, version_id),
        )
        return cursor.rowcount or 0

    def facts_for_chunk(self, chunk_id: str, *, current_only: bool = True) -> list[Fact]:
        query = "SELECT * FROM facts WHERE chunk_id = ?"
        if current_only:
            query += " AND superseded_by IS NULL"
        rows = self.connection.execute(query, (chunk_id,)).fetchall()
        return [_row_to_fact(row) for row in rows]

    def fact_count(self, *, current_only: bool = True) -> int:
        query = "SELECT COUNT(*) AS n FROM facts"
        if current_only:
            query += " WHERE superseded_by IS NULL"
        row = self.connection.execute(query).fetchone()
        return int(row["n"])

    def set_fact_concepts(self, fact_id: str, *, subject_concept: str, object_concept: str) -> None:
        self.connection.execute(
            "UPDATE facts SET subject_concept = ?, object_concept = ? WHERE id = ?",
            (subject_concept, object_concept, fact_id),
        )

    def record_rejections(
        self, run_id: str, chunk_id: str, rejections: Iterable[tuple[str, str]]
    ) -> None:
        """Store what the model claimed and we refused, with the reason."""
        self.connection.executemany(
            "INSERT INTO rejections (run_id, chunk_id, reason, detail) VALUES (?, ?, ?, ?)",
            [(run_id, chunk_id, reason, detail) for reason, detail in rejections],
        )

    def rejection_counts(self, run_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT reason, COUNT(*) AS n FROM rejections WHERE run_id = ? GROUP BY reason",
            (run_id,),
        ).fetchall()
        return {row["reason"]: int(row["n"]) for row in rows}

    # -- vocabulary and canonical concepts ----------------------------------

    def vocabulary(
        self, limit: int = DEFAULT_VOCABULARY_LIMIT
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The most-used types and predicates, to prime the extraction prompt."""
        type_rows = self.connection.execute(
            "SELECT type AS term, SUM(occurrence_count) AS n FROM canonical_concepts "
            "GROUP BY type ORDER BY n DESC, term LIMIT ?",
            (limit,),
        ).fetchall()
        predicate_rows = self.connection.execute(
            "SELECT predicate AS term, COUNT(*) AS n FROM facts "
            "WHERE superseded_by IS NULL GROUP BY predicate ORDER BY n DESC, term LIMIT ?",
            (limit,),
        ).fetchall()
        return (
            tuple(row["term"] for row in type_rows),
            tuple(row["term"] for row in predicate_rows),
        )

    def known_concepts(self, limit: int | None = None) -> list[CanonicalConcept]:
        query = "SELECT * FROM canonical_concepts ORDER BY occurrence_count DESC, label"
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.connection.execute(query, params).fetchall()
        return [
            CanonicalConcept(
                id=row["id"],
                label=row["label"],
                type=row["type"],
                occurrence_count=row["occurrence_count"],
                promoted=bool(row["promoted"]),
            )
            for row in rows
        ]

    def bump_concept(self, concept: CanonicalConcept, *, by: int = 1) -> None:
        """Insert a concept, or count another sighting of it.

        The occurrence count is what drives promotion: a type seen once stays
        marginal, a type seen twenty times becomes first-class in the UI. The
        ontology emerges from actual use rather than from a decision.
        """
        self.connection.execute(
            "INSERT INTO canonical_concepts (id, label, type, occurrence_count, promoted) "
            "VALUES (?, ?, ?, ?, 0) ON CONFLICT(id) DO UPDATE SET "
            "occurrence_count = occurrence_count + excluded.occurrence_count",
            (concept.id, concept.label, concept.type, by),
        )

    def upsert_alias(self, alias: Alias) -> None:
        self.connection.execute(
            "INSERT INTO aliases (raw_label, raw_type, canonical_id, method, score) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(raw_label, raw_type) DO UPDATE SET "
            "canonical_id = excluded.canonical_id, method = excluded.method, "
            "score = excluded.score",
            (
                alias.raw_label,
                alias.raw_type,
                alias.canonical_id,
                alias.method,
                alias.score,
            ),
        )

    def concept_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM canonical_concepts").fetchone()
        return int(row["n"])

    def chunk_sources(self, chunk_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
        """Map chunk ids to ``(vault path, event time)`` for citation display.

        A retrieved passage that cannot be traced back to a file and a date is
        not evidence, it is trivia. Every answer cites through this.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.connection.execute(
            f"SELECT c.id, d.relative_path, v.event_time FROM chunks c "  # noqa: S608
            f"JOIN document_versions v ON v.id = c.document_version_id "
            f"JOIN documents d ON d.id = v.document_id "
            f"WHERE c.id IN ({placeholders})",
            tuple(chunk_ids),
        ).fetchall()
        return {row["id"]: (row["relative_path"], row["event_time"]) for row in rows}

    # -- projection ---------------------------------------------------------

    def facts_for_projection(self) -> list[sqlite3.Row]:
        """Current, canonicalised facts, ready to be written into the graph.

        Facts whose concepts have not been resolved yet are skipped rather than
        projected under their raw labels: the graph only ever shows canonical
        nodes, and it is rebuilt from scratch whenever that changes.
        """
        return self.connection.execute(
            "SELECT f.id AS fact_id, f.predicate, f.confidence, f.event_time, f.chunk_id, "
            "f.subject_concept, f.object_concept, "
            "sc.label AS subject_label, sc.type AS subject_type, "
            "oc.label AS object_label, oc.type AS object_type "
            "FROM facts f "
            "JOIN canonical_concepts sc ON sc.id = f.subject_concept "
            "JOIN canonical_concepts oc ON oc.id = f.object_concept "
            "WHERE f.superseded_by IS NULL"
        ).fetchall()

    def find_concept_by_label(self, label: str) -> CanonicalConcept | None:
        """Look up a concept by canonical or aliased label. Case-insensitive."""
        row = self.connection.execute(
            "SELECT c.* FROM canonical_concepts c WHERE c.label = ? COLLATE NOCASE "
            "UNION ALL "
            "SELECT c.* FROM canonical_concepts c JOIN aliases a ON a.canonical_id = c.id "
            "WHERE a.raw_label = ? COLLATE NOCASE LIMIT 1",
            (label, label),
        ).fetchone()
        if row is None:
            return None
        return CanonicalConcept(
            id=row["id"],
            label=row["label"],
            type=row["type"],
            occurrence_count=row["occurrence_count"],
            promoted=bool(row["promoted"]),
        )
