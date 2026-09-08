"""Schema migrations.

Forward-only and additive. The store is derived data, so in the worst case it
can be dropped and rebuilt from the vault -- but that costs a full night of
model time, so we migrate properly instead.

Two rules for anything added here:

* Never write a ``DELETE`` or a destructive ``ALTER``. Facts are superseded, not
  removed. A migration that loses a row is a bug, even on derived data.
* Never edit an existing migration once it has been merged. Add a new one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from diairy.core.clock import to_iso, utc_now

Migration = tuple[int, str, Sequence[str]]

MIGRATIONS: tuple[Migration, ...] = (
    (
        1,
        "initial schema",
        (
            """
            CREATE TABLE meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE documents (
                id            TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                first_seen_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE document_versions (
                id           TEXT PRIMARY KEY,
                document_id  TEXT NOT NULL REFERENCES documents(id),
                content_hash TEXT NOT NULL,
                byte_size    INTEGER NOT NULL,
                ingested_at  TEXT NOT NULL,
                event_time   TEXT NOT NULL,
                git_commit   TEXT
            )
            """,
            "CREATE INDEX idx_versions_document ON document_versions(document_id)",
            """
            CREATE TABLE chunks (
                id                  TEXT PRIMARY KEY,
                document_version_id TEXT NOT NULL REFERENCES document_versions(id),
                ordinal             INTEGER NOT NULL,
                char_start          INTEGER NOT NULL,
                char_end            INTEGER NOT NULL,
                text                TEXT NOT NULL,
                heading_path        TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX idx_chunks_version ON chunks(document_version_id)",
            """
            CREATE TABLE runs (
                id              TEXT PRIMARY KEY,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                status          TEXT NOT NULL,
                model           TEXT NOT NULL DEFAULT '',
                profile         TEXT NOT NULL DEFAULT '',
                prompt_version  TEXT NOT NULL DEFAULT '',
                pipeline_version TEXT NOT NULL DEFAULT '',
                stats           TEXT NOT NULL DEFAULT '{}',
                error           TEXT
            )
            """,
            """
            CREATE TABLE facts (
                id                TEXT PRIMARY KEY,
                subject_label     TEXT NOT NULL,
                subject_type      TEXT NOT NULL,
                subject_concept   TEXT,
                predicate         TEXT NOT NULL,
                object_label      TEXT NOT NULL,
                object_type       TEXT NOT NULL,
                object_concept    TEXT,
                object_is_literal INTEGER NOT NULL DEFAULT 0,
                confidence        REAL NOT NULL,
                chunk_id          TEXT NOT NULL REFERENCES chunks(id),
                char_start        INTEGER NOT NULL,
                char_end          INTEGER NOT NULL,
                quote             TEXT NOT NULL,
                run_id            TEXT NOT NULL REFERENCES runs(id),
                event_time        TEXT NOT NULL,
                knowledge_time    TEXT NOT NULL,
                superseded_by     TEXT
            )
            """,
            "CREATE INDEX idx_facts_chunk ON facts(chunk_id)",
            "CREATE INDEX idx_facts_run ON facts(run_id)",
            "CREATE INDEX idx_facts_event_time ON facts(event_time)",
            "CREATE INDEX idx_facts_current ON facts(superseded_by) WHERE superseded_by IS NULL",
            "CREATE INDEX idx_facts_subject ON facts(subject_concept)",
            "CREATE INDEX idx_facts_object ON facts(object_concept)",
            "CREATE INDEX idx_facts_predicate ON facts(predicate)",
            """
            CREATE TABLE canonical_concepts (
                id               TEXT PRIMARY KEY,
                label            TEXT NOT NULL,
                type             TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 0,
                promoted         INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX idx_concepts_type ON canonical_concepts(type)",
            """
            CREATE TABLE aliases (
                raw_label    TEXT NOT NULL,
                raw_type     TEXT NOT NULL,
                canonical_id TEXT NOT NULL REFERENCES canonical_concepts(id),
                method       TEXT NOT NULL,
                score        REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY (raw_label, raw_type)
            )
            """,
            """
            CREATE TABLE rejections (
                run_id   TEXT NOT NULL REFERENCES runs(id),
                chunk_id TEXT NOT NULL,
                reason   TEXT NOT NULL,
                detail   TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX idx_rejections_run ON rejections(run_id)",
            # remove_diacritics 2 matters: without it, French queries miss every
            # accented word the user typed without an accent, and vice versa.
            """
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                text,
                tokenize='unicode61 remove_diacritics 2'
            )
            """,
        ),
    ),
)

CURRENT_VERSION = max(version for version, _, _ in MIGRATIONS)


def _applied_versions(connection: sqlite3.Connection) -> set[int]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def migrate(connection: sqlite3.Connection) -> list[int]:
    """Apply every pending migration. Returns the versions applied."""
    applied = _applied_versions(connection)
    newly_applied: list[int] = []
    for version, name, statements in sorted(MIGRATIONS):
        if version in applied:
            continue
        with connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, to_iso(utc_now())),
            )
        newly_applied.append(version)
    return newly_applied
