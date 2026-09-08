"""The knowledge graph, as a projection.

Kuzu is an embedded graph database: a directory on disk, no daemon, no Docker,
Cypher-compatible. It fits the "clone it and it runs" constraint that a server
would break.

The important design point is not the engine, though. It is that this graph is
*derived*. The fact log in SQLite is the truth; this is a materialised view of
it, and it can be deleted and rebuilt at any time. Deleting it loses nothing,
which is exactly why swapping Kuzu for Neo4j later is an evening of work rather
than a migration project.

That is also why the no-deletion rule does not apply here: there is nothing to
lose. It applies, absolutely, to the fact log.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diairy.core.errors import StoreError

_SCHEMA = (
    """
    CREATE NODE TABLE IF NOT EXISTS Concept(
        id STRING,
        label STRING,
        type STRING,
        PRIMARY KEY (id)
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS Claims(
        FROM Concept TO Concept,
        fact_id STRING,
        predicate STRING,
        confidence DOUBLE,
        event_time STRING,
        chunk_id STRING
    )
    """,
)


@dataclass(frozen=True)
class ProjectionStats:
    """What one projection pass wrote."""

    concepts: int = 0
    edges: int = 0


def graph_backend_available() -> bool:
    """Whether the graph engine can be imported on this machine."""
    try:
        import kuzu  # noqa: F401, PLC0415 -- probing availability
    except ImportError:
        return False
    return True


class GraphProjection:
    """A Kuzu database holding the current state of the knowledge graph."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._database: Any = None
        self._connection: Any = None

    def connect(self) -> None:
        """Open the graph database, creating it and its schema if needed."""
        if self._connection is not None:
            return
        try:
            import kuzu  # noqa: PLC0415 -- optional heavy import
        except ImportError as exc:
            message = (
                "The graph engine (kuzu) is not installed. Install it, or run with "
                "--no-graph to use the relational store only."
            )
            raise StoreError(message) from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._database = kuzu.Database(str(self.path))
            self._connection = kuzu.Connection(self._database)
            for statement in _SCHEMA:
                self._connection.execute(statement)
        except RuntimeError as exc:
            message = f"Could not open the graph at {self.path}: {exc}"
            raise StoreError(message) from exc

    def close(self) -> None:
        self._connection = None
        self._database = None

    def drop(self) -> None:
        """Delete the projection. Safe: it is rebuilt from the fact log."""
        self.close()
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

    def _require_connection(self) -> Any:
        if self._connection is None:
            self.connect()
        return self._connection

    def upsert_concept(self, concept_id: str, label: str, type_name: str) -> None:
        connection = self._require_connection()
        connection.execute(
            "MERGE (c:Concept {id: $id}) SET c.label = $label, c.type = $type",
            {"id": concept_id, "label": label, "type": type_name},
        )

    def upsert_edge(
        self,
        *,
        subject_id: str,
        object_id: str,
        fact_id: str,
        predicate: str,
        confidence: float,
        event_time: str,
        chunk_id: str,
    ) -> None:
        connection = self._require_connection()
        connection.execute(
            "MATCH (s:Concept {id: $subject}), (o:Concept {id: $object}) "
            "MERGE (s)-[r:Claims {fact_id: $fact_id}]->(o) "
            "SET r.predicate = $predicate, r.confidence = $confidence, "
            "r.event_time = $event_time, r.chunk_id = $chunk_id",
            {
                "subject": subject_id,
                "object": object_id,
                "fact_id": fact_id,
                "predicate": predicate,
                "confidence": confidence,
                "event_time": event_time,
                "chunk_id": chunk_id,
            },
        )

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a Cypher query and return rows as dictionaries."""
        connection = self._require_connection()
        try:
            result = connection.execute(cypher, parameters or {})
        except RuntimeError as exc:
            message = f"Graph query failed: {exc}"
            raise StoreError(message) from exc
        columns = result.get_column_names()
        rows: list[dict[str, Any]] = []
        while result.has_next():
            rows.append(dict(zip(columns, result.get_next(), strict=True)))
        return rows

    def concept_count(self) -> int:
        rows = self.query("MATCH (c:Concept) RETURN COUNT(c) AS n")
        return int(rows[0]["n"]) if rows else 0

    def edge_count(self) -> int:
        rows = self.query("MATCH ()-[r:Claims]->() RETURN COUNT(r) AS n")
        return int(rows[0]["n"]) if rows else 0

    def neighbours(self, concept_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """One-hop neighbourhood of a concept, in both directions."""
        return self.query(
            "MATCH (c:Concept {id: $id})-[r:Claims]-(other:Concept) "
            "RETURN other.id AS id, other.label AS label, other.type AS type, "
            "r.predicate AS predicate, r.confidence AS confidence, "
            "r.fact_id AS fact_id, r.chunk_id AS chunk_id "
            "ORDER BY r.confidence DESC LIMIT $limit",
            {"id": concept_id, "limit": limit},
        )
