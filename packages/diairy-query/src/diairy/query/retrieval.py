"""Finding the passages that could answer a question.

Hybrid search gives us the passages. The graph gives us the facts around the
concepts those passages mention. Both are handed to the model, and both carry
their provenance, so the answer can be checked rather than believed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from diairy.core.clock import from_iso
from diairy.nlp.provider import EmbeddingProvider
from diairy.store.graph import GraphProjection
from diairy.store.repository import Repository
from diairy.store.search import SearchHit, SearchIndex

DEFAULT_PASSAGE_LIMIT = 8
DEFAULT_FACT_LIMIT = 20


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk, ready to be shown to a model or a human."""

    index: int
    chunk_id: str
    text: str
    source_path: str
    event_time: str
    score: float
    found_by_both: bool = False

    @property
    def event_date(self) -> str:
        try:
            return from_iso(self.event_time).date().isoformat()
        except ValueError:
            return self.event_time


@dataclass(frozen=True)
class RetrievedContext:
    """Everything gathered for one question."""

    question: str
    passages: list[Passage] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    semantic_search_used: bool = False

    def render_passages(self) -> str:
        if not self.passages:
            return "(no matching entries)"
        return "\n\n".join(
            f"[{passage.index}] ({passage.event_date}, {passage.source_path})\n{passage.text}"
            for passage in self.passages
        )

    def render_facts(self) -> str:
        return "\n".join(self.facts) if self.facts else "(none)"


class Retriever:
    """Gathers passages and related graph facts for a question."""

    def __init__(
        self,
        *,
        repository: Repository,
        search: SearchIndex,
        embedder: EmbeddingProvider | None = None,
        graph: GraphProjection | None = None,
    ) -> None:
        self.repository = repository
        self.search = search
        self.embedder = embedder
        self.graph = graph

    def retrieve(
        self,
        question: str,
        *,
        limit: int = DEFAULT_PASSAGE_LIMIT,
        fact_limit: int = DEFAULT_FACT_LIMIT,
    ) -> RetrievedContext:
        """Retrieve passages, then the facts attached to them."""
        vector: list[float] | None = None
        if self.embedder is not None and self.search.vector_table_exists():
            vector = self.embedder.embed([question])[0]

        hits = self.search.hybrid(question, vector=vector, limit=limit)
        sources = self.repository.chunk_sources([hit.chunk_id for hit in hits])
        passages = [
            self._to_passage(index, hit, sources) for index, hit in enumerate(hits, start=1)
        ]
        return RetrievedContext(
            question=question,
            passages=passages,
            facts=self._facts_for(passages, limit=fact_limit),
            semantic_search_used=vector is not None,
        )

    def _to_passage(
        self,
        index: int,
        hit: SearchHit,
        sources: dict[str, tuple[str, str]],
    ) -> Passage:
        path, event_time = sources.get(hit.chunk_id, ("(unknown)", ""))
        return Passage(
            index=index,
            chunk_id=hit.chunk_id,
            text=hit.text,
            source_path=path,
            event_time=event_time,
            score=hit.score,
            found_by_both=hit.found_by_both,
        )

    def _facts_for(self, passages: list[Passage], *, limit: int) -> list[str]:
        """The facts extracted from the retrieved passages, as readable lines."""
        lines: list[str] = []
        for passage in passages:
            for fact in self.repository.facts_for_chunk(passage.chunk_id):
                lines.append(
                    f"- {fact.subject.label} ({fact.subject.type}) "
                    f"--{fact.predicate}--> {fact.object.label} ({fact.object.type}) "
                    f"[{passage.index}]"
                )
                if len(lines) >= limit:
                    return lines
        return lines
