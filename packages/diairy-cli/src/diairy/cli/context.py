"""Wiring the application together.

One place builds every object the commands need, and it is also the place that
arms the egress guard. Nothing in diAIry opens a socket before this runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from diairy.core.config import Settings, load_settings
from diairy.core.egress import install_egress_guard
from diairy.nlp.canonical import Canonicalizer
from diairy.nlp.extract import FactExtractor
from diairy.nlp.factory import build_embedder, build_llm, resolve_profile
from diairy.nlp.provider import EmbeddingProvider, LLMProvider
from diairy.nlp.registry import Profile
from diairy.pipeline.runner import Pipeline
from diairy.query.answer import Answerer
from diairy.query.retrieval import Retriever
from diairy.store.db import connect
from diairy.store.graph import GraphProjection, graph_backend_available
from diairy.store.repository import Repository
from diairy.store.search import SearchIndex


@dataclass
class AppContext:
    """Everything a command might need, built once."""

    settings: Settings
    profile: Profile
    repository: Repository
    search: SearchIndex
    llm: LLMProvider
    embedder: EmbeddingProvider
    graph: GraphProjection | None

    def pipeline(self) -> Pipeline:
        return Pipeline(
            settings=self.settings,
            repository=self.repository,
            search=self.search,
            extractor=FactExtractor(self.llm),
            embedder=self.embedder,
            canonicalizer=Canonicalizer(
                threshold=self.settings.canonicalization_threshold,
                embedder=self.embedder,
            ),
            graph=self.graph,
        )

    def retriever(self) -> Retriever:
        return Retriever(
            repository=self.repository,
            search=self.search,
            embedder=self.embedder,
            graph=self.graph,
        )

    def answerer(self) -> Answerer:
        return Answerer(llm=self.llm, retriever=self.retriever())

    def close(self) -> None:
        if self.graph is not None:
            self.graph.close()
        self.repository.connection.close()

    def __enter__(self) -> AppContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def build_context(*, with_graph: bool = True) -> AppContext:
    """Load configuration, arm the egress guard and open every store."""
    settings = load_settings()
    settings.ensure_directories()

    # The one address allowed out of this process, and only because the model
    # server lives there. Everything else raises.
    install_egress_guard(allow_loopback=True, allowlist=[settings.ollama_address])

    profile = resolve_profile(settings)
    connection = connect(settings.db_path)
    graph = (
        GraphProjection(settings.graph_path) if with_graph and graph_backend_available() else None
    )
    return AppContext(
        settings=settings,
        profile=profile,
        repository=Repository(connection),
        search=SearchIndex(connection),
        llm=build_llm(profile.extraction, settings),
        embedder=build_embedder(profile.embedding, settings),
        graph=graph,
    )
