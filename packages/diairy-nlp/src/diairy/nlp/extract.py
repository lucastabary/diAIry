"""Turning a chunk of journal into verified claims.

The model proposes; this module disposes. Every claim comes back with a quote,
and a claim whose quote cannot be found verbatim in the source is dropped
without appeal. Rejections are counted and logged rather than silently
swallowed, because the rejection rate is the single best health signal we have
for a given model and prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from diairy.core.clock import utc_now
from diairy.core.ids import fact_id
from diairy.core.models import Chunk, ConceptRef, Evidence, Fact
from diairy.core.text import locate_quote
from diairy.nlp.enrich import extract_signals, format_signals
from diairy.nlp.prompts import load_prompt
from diairy.nlp.provider import LLMProvider, LLMResponse
from diairy.nlp.schema import ExtractionResult, RawFact, extraction_json_schema

MAX_VOCABULARY_TERMS = 60


@dataclass(frozen=True)
class Vocabulary:
    """The type and predicate names already present in the graph.

    Shown to the model so it converges on terms it has used before instead of
    inventing a new word for the same idea every night. This is a nudge, not a
    constraint: the ontology stays open, it just stops drifting.
    """

    types: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()

    def render(self, limit: int = MAX_VOCABULARY_TERMS) -> str:
        if not self.types and not self.predicates:
            return "(empty -- this is the first pass over this vault)"
        types = ", ".join(self.types[:limit]) or "(none yet)"
        predicates = ", ".join(self.predicates[:limit]) or "(none yet)"
        return f"types: {types}\npredicates: {predicates}"


@dataclass(frozen=True)
class RejectedFact:
    """A claim the model made that we refused to store, and why."""

    reason: str
    detail: str


@dataclass
class ExtractionOutcome:
    """Everything one call to the model produced."""

    facts: list[Fact] = field(default_factory=list)
    rejected: list[RejectedFact] = field(default_factory=list)
    response: LLMResponse | None = None
    prompt_version: str = ""

    @property
    def rejection_rate(self) -> float:
        total = len(self.facts) + len(self.rejected)
        return len(self.rejected) / total if total else 0.0


class FactExtractor:
    """Extracts verified facts from chunks using an LLM provider."""

    def __init__(self, llm: LLMProvider, *, prompt_name: str = "extraction") -> None:
        self._llm = llm
        self._prompt = load_prompt(prompt_name)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    @property
    def model(self) -> str:
        """The model behind this extractor, recorded on every run."""
        return self._llm.model

    def build_prompt(self, chunk: Chunk, vocabulary: Vocabulary, event_date: str) -> str:
        """Render the user message for a chunk. Public so tests can assert on it."""
        signals = extract_signals(chunk.text)
        return self._prompt.render(
            vocabulary=vocabulary.render(),
            signals=format_signals(signals),
            heading_path=" > ".join(chunk.heading_path) or "(top level)",
            event_date=event_date,
            text=chunk.text,
        )

    def extract(
        self,
        chunk: Chunk,
        *,
        run_id: str,
        event_time: datetime,
        vocabulary: Vocabulary | None = None,
    ) -> ExtractionOutcome:
        """Extract facts from one chunk.

        ``event_time`` is when the entry was written; it becomes the event time
        of every fact drawn from it. The knowledge time is now.
        """
        vocabulary = vocabulary or Vocabulary()
        prompt = self.build_prompt(chunk, vocabulary, event_time.date().isoformat())
        response = self._llm.complete(
            system=self._prompt.system,
            prompt=prompt,
            json_schema=extraction_json_schema(),
        )

        outcome = ExtractionOutcome(response=response, prompt_version=self._prompt.version)
        parsed = self._parse(response.text, outcome)
        if parsed is None:
            return outcome

        knowledge_time = utc_now()
        seen: set[str] = set()
        for raw in parsed.facts:
            fact = self._validate(
                raw,
                chunk=chunk,
                run_id=run_id,
                event_time=event_time,
                knowledge_time=knowledge_time,
                outcome=outcome,
            )
            if fact is None or fact.id in seen:
                continue
            seen.add(fact.id)
            outcome.facts.append(fact)
        return outcome

    def _parse(self, text: str, outcome: ExtractionOutcome) -> ExtractionResult | None:
        """Parse the model response, recording a rejection instead of raising.

        A single malformed response must not abort a whole nightly run over
        hundreds of chunks.
        """
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            outcome.rejected.append(RejectedFact(reason="unparseable_response", detail=str(exc)))
            return None
        try:
            return ExtractionResult.model_validate(payload)
        except ValidationError as exc:
            outcome.rejected.append(RejectedFact(reason="schema_violation", detail=str(exc)[:500]))
            return None

    def _validate(
        self,
        raw: RawFact,
        *,
        chunk: Chunk,
        run_id: str,
        event_time: datetime,
        knowledge_time: datetime,
        outcome: ExtractionOutcome,
    ) -> Fact | None:
        """Verify one claim against the source text, or reject it."""
        span = locate_quote(chunk.text, raw.quote)
        if span is None:
            outcome.rejected.append(
                RejectedFact(
                    reason="quote_not_found",
                    detail=f"{raw.quote[:120]!r} is not in the source text",
                )
            )
            return None

        relative_start, relative_end = span
        char_start = chunk.char_start + relative_start
        char_end = chunk.char_start + relative_end

        identifier = fact_id(
            chunk=chunk.id,
            subject=raw.subject.label,
            predicate=raw.predicate,
            obj=raw.object.label,
            char_start=char_start,
            char_end=char_end,
        )
        return Fact(
            id=identifier,
            subject=ConceptRef(label=raw.subject.label, type=raw.subject.type),
            predicate=raw.predicate,
            object=ConceptRef(label=raw.object.label, type=raw.object.type),
            object_is_literal=raw.object_is_literal,
            confidence=raw.confidence,
            evidence=Evidence(
                chunk_id=chunk.id,
                char_start=char_start,
                char_end=char_end,
                quote=chunk.text[relative_start:relative_end],
            ),
            run_id=run_id,
            event_time=event_time,
            knowledge_time=knowledge_time,
        )
