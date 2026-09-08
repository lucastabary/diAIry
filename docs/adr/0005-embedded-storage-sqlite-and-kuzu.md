# 5. Embedded storage: SQLite and Kuzu

Status: Accepted

## Context

The system needs a relational store, full-text search, vector search and a
graph. It must install in one step on both an Apple Silicon Mac and a Windows
laptop, run with no daemon competing with a local model for RAM, and be portable
enough to live on a USB stick.

## Decision

- **SQLite** for the fact log, with **FTS5** for lexical search and
  **sqlite-vec** for vectors. One file holds all three.
- **Kuzu** for the graph: embedded, Cypher-compatible, a directory on disk.
- The graph and the vector index are *projections* of the fact log, and can be
  dropped and rebuilt at any time.

Retrieval fuses BM25 and vector rankings with Reciprocal Rank Fusion, which
needs no score calibration between two incomparable scales.

## Alternatives considered

**PostgreSQL with pgvector and Apache AGE.** One engine for everything, mature,
excellent. Requires a running server on a personal laptop, which breaks both the
one-step install and the USB-stick portability.

**Neo4j.** The most mature graph database, with far better visualisation than
Kuzu. Needs a JVM or Docker and idles at 1-2 GB, in direct competition with the
model. Rejected reluctantly, and cheap to revisit: because the graph is a
projection, switching is an evening of work rather than a migration.

**SQLite alone, with recursive CTEs.** Zero new dependencies, and multi-hop
traversal written by hand every time.

**LanceDB or Qdrant for vectors.** Both are good, and both are sized for a
problem we do not have. A decade of journalling is perhaps 100k chunks, which
sqlite-vec brute-forces in milliseconds.

## Consequences

- No daemon, no Docker, no server. Clone and run.
- One file to back up, and the whole store fits on a USB stick.
- Kuzu is younger than Neo4j and its tooling is thinner. Accepted, because the
  projection design makes the choice reversible.
- Vector search is optional at runtime: if sqlite-vec cannot load, search
  degrades to full text and says so rather than failing.
- Both indexes sit behind interfaces (`SearchIndex`, `GraphProjection`), so a
  replacement is an adapter.
