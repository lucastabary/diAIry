"""The derived store: append-only facts, hybrid search and the graph projection."""

from diairy.store.db import connect, load_vector_extension, transaction
from diairy.store.graph import GraphProjection, ProjectionStats, graph_backend_available
from diairy.store.migrations import CURRENT_VERSION, migrate
from diairy.store.repository import PendingChunk, Repository
from diairy.store.search import SearchHit, SearchIndex, build_fts_query

__all__ = [
    "CURRENT_VERSION",
    "GraphProjection",
    "PendingChunk",
    "ProjectionStats",
    "Repository",
    "SearchHit",
    "SearchIndex",
    "build_fts_query",
    "connect",
    "graph_backend_available",
    "load_vector_extension",
    "migrate",
    "transaction",
]
