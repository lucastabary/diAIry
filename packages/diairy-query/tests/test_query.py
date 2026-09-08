"""Retrieval and cited answers."""

from __future__ import annotations

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
from diairy.nlp.provider import LLMResponse
from diairy.query.answer import NO_RESULTS_MESSAGE, Answerer
from diairy.query.retrieval import Retriever
from diairy.store.db import connect
from diairy.store.repository import Repository
from diairy.store.search import SearchIndex

NOW = datetime(2026, 3, 14, tzinfo=UTC)
TEXT = "J'ai fini Le Nom de la rose hier soir, Eco reste imbattable."


class StubLLM:
    """Answers with a fixed sentence, so the test asserts on our plumbing."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str:
        return "stub"

    def complete(self, *, system: str, prompt: str, **_: object) -> LLMResponse:  # noqa: ARG002
        self.prompts.append(prompt)
        return LLMResponse(text=self.text, model="stub")


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Repository]:
    connection = connect(tmp_path / "q.db")
    repo = Repository(connection)
    repo.upsert_document(Document(id="d1", relative_path="2026/03-14.md", first_seen_at=NOW))
    repo.insert_version(
        DocumentVersion(
            id="v1",
            document_id="d1",
            content_hash="h",
            byte_size=len(TEXT),
            ingested_at=NOW,
            event_time=NOW,
        )
    )
    repo.insert_chunks(
        [
            Chunk(
                id="c1",
                document_version_id="v1",
                ordinal=0,
                char_start=0,
                char_end=len(TEXT),
                text=TEXT,
            )
        ]
    )
    repo.start_run(Run(id="r1", started_at=NOW))
    repo.insert_facts(
        [
            Fact(
                id="f1",
                subject=ConceptRef(label="moi", type="personne"),
                predicate="a fini",
                object=ConceptRef(label="Le Nom de la rose", type="livre"),
                confidence=0.9,
                evidence=Evidence(chunk_id="c1", char_start=0, char_end=9, quote="J'ai fini"),
                run_id="r1",
                event_time=NOW,
                knowledge_time=NOW,
            )
        ]
    )
    yield repo
    connection.close()


def _retriever(repository: Repository) -> Retriever:
    return Retriever(repository=repository, search=SearchIndex(repository.connection))


def test_retrieval_numbers_passages_and_attaches_their_source(
    repository: Repository,
) -> None:
    context = _retriever(repository).retrieve("Eco")
    assert len(context.passages) == 1
    passage = context.passages[0]
    assert passage.index == 1
    assert passage.source_path == "2026/03-14.md"
    assert passage.event_date == "2026-03-14"
    assert not context.semantic_search_used  # no embedder configured


def test_retrieval_includes_the_facts_of_the_retrieved_passages(
    repository: Repository,
) -> None:
    context = _retriever(repository).retrieve("Eco")
    assert any("a fini" in line for line in context.facts)
    assert "[1]" in context.render_facts()


def test_rendered_passages_carry_date_path_and_text(repository: Repository) -> None:
    rendered = _retriever(repository).retrieve("Eco").render_passages()
    assert "[1]" in rendered
    assert "2026-03-14" in rendered
    assert "Eco reste imbattable" in rendered


def test_answer_exposes_only_the_passages_it_cited(repository: Repository) -> None:
    llm = StubLLM("Tu l'as fini le 14 mars [1].")
    answer = Answerer(llm=llm, retriever=_retriever(repository)).ask("Quand ai-je fini Eco ?")
    assert answer.cited_indexes == {1}
    assert [passage.index for passage in answer.cited_passages] == [1]
    assert not answer.is_uncited


def test_an_uncited_answer_is_flagged(repository: Repository) -> None:
    llm = StubLLM("Tu l'as fini au printemps, je crois.")
    answer = Answerer(llm=llm, retriever=_retriever(repository)).ask("Eco")
    assert answer.passages  # a passage was retrieved and shown to the model
    assert answer.is_uncited  # but the answer points at none of it


def test_no_matching_passage_means_no_model_call(repository: Repository) -> None:
    """Never ask a model to answer from nothing: that is how invention starts."""
    llm = StubLLM("je vais inventer")
    answer = Answerer(llm=llm, retriever=_retriever(repository)).ask("zzzzz introuvable")
    assert answer.text == NO_RESULTS_MESSAGE
    assert answer.passages == []
    assert llm.prompts == []


def test_the_prompt_contains_the_passages_and_the_question(repository: Repository) -> None:
    llm = StubLLM("[1]")
    Answerer(llm=llm, retriever=_retriever(repository)).ask("Quand ai-je fini Eco ?")
    (prompt,) = llm.prompts
    assert "Eco reste imbattable" in prompt
    assert "Quand ai-je fini Eco ?" in prompt


def test_semantic_search_is_used_when_embeddings_exist(repository: Repository) -> None:
    from diairy.nlp.fake import HashingEmbeddings

    embedder = HashingEmbeddings(dimensions=32)
    index = SearchIndex(repository.connection)
    if not index.ensure_vector_table(32, "hashing"):
        pytest.skip("sqlite-vec is not loadable in this interpreter")
    index.index_embeddings([("c1", embedder.embed([TEXT])[0])])

    retriever = Retriever(repository=repository, search=index, embedder=embedder)
    context = retriever.retrieve("Eco")
    assert context.semantic_search_used
    assert context.passages[0].found_by_both
