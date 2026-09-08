"""Extraction: what we accept from the model, and what we refuse."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from diairy.core.models import Chunk
from diairy.nlp.extract import ExtractionOutcome, FactExtractor, Vocabulary
from diairy.nlp.fake import ScriptedLLM

CHUNK_TEXT = (
    "J'ai fini Le Nom de la rose hier soir. "
    "Eco arrive a rendre la semiotique excitante, ce qui tient du miracle."
)
EVENT_TIME = datetime(2026, 3, 14, tzinfo=UTC)

CHUNK = Chunk(
    id="chunk-1",
    document_version_id="version-1",
    ordinal=0,
    char_start=100,  # non-zero on purpose: absolute offsets must be respected
    char_end=100 + len(CHUNK_TEXT),
    text=CHUNK_TEXT,
    heading_path=("Lectures",),
)


def _response(*facts: dict[str, object]) -> str:
    return json.dumps({"facts": list(facts)})


def _fact(
    subject: str = "moi",
    predicate: str = "a fini",
    obj: str = "Le Nom de la rose",
    quote: str = "J'ai fini Le Nom de la rose hier soir.",
    confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "subject": {"label": subject, "type": "personne"},
        "predicate": predicate,
        "object": {"label": obj, "type": "livre"},
        "object_is_literal": False,
        "confidence": confidence,
        "quote": quote,
    }


def _extract(response: str) -> tuple[FactExtractor, ExtractionOutcome]:
    llm = ScriptedLLM(responses=[response])
    extractor = FactExtractor(llm)
    return extractor, extractor.extract(CHUNK, run_id="run-1", event_time=EVENT_TIME)


def test_a_supported_claim_is_accepted_with_absolute_offsets() -> None:
    _, outcome = _extract(_response(_fact()))
    assert len(outcome.facts) == 1
    fact = outcome.facts[0]
    assert fact.subject.label == "moi"
    assert fact.object.label == "Le Nom de la rose"
    assert fact.evidence.char_start == CHUNK.char_start
    assert fact.evidence.quote == "J'ai fini Le Nom de la rose hier soir."
    assert fact.event_time == EVENT_TIME


def test_an_invented_quote_is_rejected() -> None:
    _, outcome = _extract(_response(_fact(quote="J'ai fini Guerre et Paix hier soir.")))
    assert outcome.facts == []
    assert [entry.reason for entry in outcome.rejected] == ["quote_not_found"]
    assert outcome.rejection_rate == 1.0


def test_a_valid_claim_survives_alongside_a_rejected_one() -> None:
    _, outcome = _extract(_response(_fact(), _fact(obj="Guerre et Paix", quote="jamais ecrit ca")))
    assert len(outcome.facts) == 1
    assert len(outcome.rejected) == 1


def test_quote_with_sloppy_whitespace_is_still_accepted() -> None:
    _, outcome = _extract(_response(_fact(quote="J'ai   fini\n  Le Nom de la rose")))
    assert len(outcome.facts) == 1


def test_identical_claims_from_the_same_evidence_are_deduplicated() -> None:
    _, outcome = _extract(_response(_fact(), _fact()))
    assert len(outcome.facts) == 1


def test_unparseable_response_does_not_raise() -> None:
    """One bad answer must not abort a batch of hundreds of chunks."""
    _, outcome = _extract("this is not json at all")
    assert outcome.facts == []
    assert outcome.rejected[0].reason == "unparseable_response"


def test_response_violating_the_schema_is_rejected() -> None:
    _, outcome = _extract(json.dumps({"facts": [{"subject": "a string, not an object"}]}))
    assert outcome.facts == []
    assert outcome.rejected[0].reason == "schema_violation"


def test_out_of_range_confidence_is_rejected_by_the_schema() -> None:
    _, outcome = _extract(_response(_fact(confidence=3.0)))
    assert outcome.facts == []
    assert outcome.rejected[0].reason == "schema_violation"


def test_empty_answer_is_a_valid_answer() -> None:
    _, outcome = _extract(_response())
    assert outcome.facts == []
    assert outcome.rejected == []
    assert outcome.rejection_rate == 0.0


def test_prompt_includes_vocabulary_signals_and_heading() -> None:
    extractor = FactExtractor(ScriptedLLM(responses=[_response()]))
    prompt = extractor.build_prompt(
        CHUNK,
        Vocabulary(types=("livre", "personne"), predicates=("a lu",)),
        "2026-03-14",
    )
    assert "livre" in prompt
    assert "a lu" in prompt
    assert "Lectures" in prompt
    assert "2026-03-14" in prompt
    assert CHUNK_TEXT in prompt


def test_empty_vocabulary_is_announced_as_such() -> None:
    assert "first pass" in Vocabulary().render()


def test_the_model_receives_the_json_schema() -> None:
    llm = ScriptedLLM(responses=[_response()])
    FactExtractor(llm).extract(CHUNK, run_id="run-1", event_time=EVENT_TIME)
    assert llm.calls  # the system prompt and user prompt were both passed


def test_running_out_of_scripted_responses_is_loud() -> None:
    from diairy.core.errors import ProviderError

    extractor = FactExtractor(ScriptedLLM(responses=[]))
    with pytest.raises(ProviderError, match="ran out of responses"):
        extractor.extract(CHUNK, run_id="run-1", event_time=EVENT_TIME)
