"""Hybrid retrieval: BM25 and vectors, fused.

Neither half is enough on its own. Vector search shrugs at proper nouns, exact
titles and jargon -- ask it for "Kùzu" and it happily returns everything about
databases. Full-text search cannot find "that thing I wrote about forgetting"
unless you remember the words you used.

So we run both and fuse the rankings with Reciprocal Rank Fusion, which needs no
score calibration between two utterly different scales and is hard to get wrong.

The vector half is optional. If sqlite-vec is unavailable, or nothing is indexed
yet, search still works and reports that it ran degraded.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from diairy.core.errors import StoreError

RRF_K = 60
"""Standard RRF damping constant; the top of each list dominates, but not totally."""

VECTOR_TABLE = "chunk_vectors"
META_DIMENSIONS = "embedding_dimensions"
META_MODEL = "embedding_model"

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SearchHit:
    """One retrieved chunk and why it surfaced."""

    chunk_id: str
    text: str
    score: float
    lexical_rank: int | None = None
    semantic_rank: int | None = None

    @property
    def found_by_both(self) -> bool:
        return self.lexical_rank is not None and self.semantic_rank is not None


def build_fts_query(text: str) -> str:
    """Turn free user text into a safe FTS5 query.

    Every token is quoted, so punctuation, apostrophes and stray operators in
    a natural-language question cannot become FTS5 syntax -- or a syntax error.
    """
    tokens = _TOKEN.findall(text)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


class SearchIndex:
    """Full-text and vector search over chunks."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # -- vector index -------------------------------------------------------

    def vector_table_exists(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (VECTOR_TABLE,),
        ).fetchone()
        return row is not None

    def ensure_vector_table(self, dimensions: int, model: str) -> bool:
        """Create the vector table, sized for this embedding model.

        Dimensions are fixed for the life of the index. Changing the embedding
        model means rebuilding it, which the pipeline detects and reports rather
        than silently mixing incompatible vectors.
        """
        recorded = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (META_DIMENSIONS,)
        ).fetchone()
        if recorded is not None and int(recorded["value"]) != dimensions:
            message = (
                f"This index holds {recorded['value']}-dimensional vectors but the "
                f"configured model produces {dimensions}. Run `diairy rebuild --vectors` "
                f"after changing the embedding model."
            )
            raise StoreError(message)

        if not self.vector_table_exists():
            try:
                self.connection.execute(
                    f"CREATE VIRTUAL TABLE {VECTOR_TABLE} USING vec0("
                    f"chunk_id TEXT PRIMARY KEY, embedding float[{dimensions}])"
                )
            except sqlite3.OperationalError:
                # sqlite-vec is not loadable here; search degrades to full text.
                return False

        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (META_DIMENSIONS, str(dimensions)),
        )
        self.connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (META_MODEL, model),
        )
        return True

    def index_embeddings(self, pairs: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Store embeddings for chunks. Returns how many were written."""
        if not pairs or not self.vector_table_exists():
            return 0
        import sqlite_vec  # noqa: PLC0415 -- only needed on the vector path

        written = 0
        for chunk_id, vector in pairs:
            self.connection.execute(
                f"DELETE FROM {VECTOR_TABLE} WHERE chunk_id = ?",  # noqa: S608
                (chunk_id,),
            )
            self.connection.execute(
                f"INSERT INTO {VECTOR_TABLE} (chunk_id, embedding) VALUES (?, ?)",  # noqa: S608
                (chunk_id, sqlite_vec.serialize_float32(list(vector))),
            )
            written += 1
        return written

    def indexed_vector_count(self) -> int:
        if not self.vector_table_exists():
            return 0
        row = self.connection.execute(
            f"SELECT COUNT(*) AS n FROM {VECTOR_TABLE}"  # noqa: S608
        ).fetchone()
        return int(row["n"])

    def unindexed_chunk_ids(self) -> list[str]:
        """Chunks with no embedding yet. The embedding stage's work queue."""
        if not self.vector_table_exists():
            rows = self.connection.execute("SELECT id FROM chunks").fetchall()
            return [row["id"] for row in rows]
        rows = self.connection.execute(
            f"SELECT c.id FROM chunks c LEFT JOIN {VECTOR_TABLE} v "  # noqa: S608
            f"ON v.chunk_id = c.id WHERE v.chunk_id IS NULL"
        ).fetchall()
        return [row["id"] for row in rows]

    # -- retrieval ----------------------------------------------------------

    def lexical(self, query: str, limit: int = 20) -> list[str]:
        """BM25-ranked chunk ids for a free-text query."""
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        rows = self.connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return [row["chunk_id"] for row in rows]

    def semantic(self, vector: Sequence[float], limit: int = 20) -> list[str]:
        """Nearest-neighbour chunk ids for a query embedding."""
        if not self.vector_table_exists():
            return []
        import sqlite_vec  # noqa: PLC0415 -- only needed on the vector path

        try:
            rows = self.connection.execute(
                f"SELECT chunk_id FROM {VECTOR_TABLE} "  # noqa: S608
                f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(list(vector)), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row["chunk_id"] for row in rows]

    def hybrid(
        self,
        query: str,
        *,
        vector: Sequence[float] | None = None,
        limit: int = 10,
        pool: int = 40,
    ) -> list[SearchHit]:
        """Fuse lexical and semantic rankings with RRF."""
        lexical_ids = self.lexical(query, limit=pool)
        semantic_ids = self.semantic(vector, limit=pool) if vector is not None else []

        scores: dict[str, float] = {}
        lexical_rank: dict[str, int] = {}
        semantic_rank: dict[str, int] = {}

        for rank, chunk_id in enumerate(lexical_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            lexical_rank[chunk_id] = rank
        for rank, chunk_id in enumerate(semantic_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            semantic_rank[chunk_id] = rank

        if not scores:
            return []

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        placeholders = ",".join("?" for _ in ordered)
        rows = self.connection.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({placeholders})",  # noqa: S608
            tuple(chunk_id for chunk_id, _ in ordered),
        ).fetchall()
        texts = {row["id"]: row["text"] for row in rows}

        return [
            SearchHit(
                chunk_id=chunk_id,
                text=texts.get(chunk_id, ""),
                score=score,
                lexical_rank=lexical_rank.get(chunk_id),
                semantic_rank=semantic_rank.get(chunk_id),
            )
            for chunk_id, score in ordered
        ]
