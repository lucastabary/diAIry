"""Answering questions, with receipts.

An answer that cannot be traced back to what the user actually wrote is worse
than no answer: it quietly rewrites their memory. So every answer carries the
passages it was built from, and the citation markers in the text point at them.

The model is never handed the whole journal. It only sees the retrieved
passages, which keeps the prompt small enough for a local model and makes the
failure mode "I could not find it" rather than confident invention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from diairy.nlp.prompts import load_prompt
from diairy.nlp.provider import LLMProvider
from diairy.query.retrieval import Passage, RetrievedContext, Retriever

_CITATION = re.compile(r"\[(\d+)\]")

NO_RESULTS_MESSAGE = "Nothing in the journal matches this question."


@dataclass(frozen=True)
class Answer:
    """A generated answer and the evidence behind it."""

    question: str
    text: str
    passages: list[Passage] = field(default_factory=list)
    semantic_search_used: bool = True

    @property
    def cited_indexes(self) -> set[int]:
        """Passage numbers the answer actually referenced."""
        return {int(match) for match in _CITATION.findall(self.text)}

    @property
    def cited_passages(self) -> list[Passage]:
        cited = self.cited_indexes
        return [passage for passage in self.passages if passage.index in cited]

    @property
    def is_uncited(self) -> bool:
        """True when the model answered without pointing at any passage.

        Not an error on its own -- "I found nothing" is uncited and correct --
        but the CLI surfaces it, because an uncited claim is exactly the kind
        the user should not take at face value.
        """
        return bool(self.passages) and not self.cited_indexes


class Answerer:
    """Retrieval-augmented question answering over the journal."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        retriever: Retriever,
        prompt_name: str = "answer",
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._prompt = load_prompt(prompt_name)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    def build_prompt(self, context: RetrievedContext) -> str:
        return self._prompt.render(
            passages=context.render_passages(),
            facts=context.render_facts(),
            question=context.question,
        )

    def ask(self, question: str, *, limit: int = 8) -> Answer:
        """Retrieve, then answer, then hand back the evidence."""
        context = self._retriever.retrieve(question, limit=limit)
        if not context.passages:
            return Answer(
                question=question,
                text=NO_RESULTS_MESSAGE,
                passages=[],
                semantic_search_used=context.semantic_search_used,
            )

        response = self._llm.complete(
            system=self._prompt.system,
            prompt=self.build_prompt(context),
        )
        return Answer(
            question=question,
            text=response.text.strip(),
            passages=context.passages,
            semantic_search_used=context.semantic_search_used,
        )
