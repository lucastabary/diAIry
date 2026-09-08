"""The contract with the extraction model.

The ontology is open: the model chooses its own node types and predicates, in
the language of the source. What is *not* open is the shape of the answer. This
schema is handed to the backend for constrained decoding, so the model can be
creative about meaning while remaining incapable of emitting malformed output.

The other non-negotiable field is ``quote``. Every claim must come with the
verbatim sentence that supports it. We then look that sentence up in the source
ourselves, and drop any claim we cannot find. It is a cheap, deterministic
hallucination filter that costs no model time.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_LABEL_CHARS = 200
MAX_QUOTE_CHARS = 600


class RawConcept(BaseModel):
    """A node as the model named it, before any canonicalisation."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    type: str = Field(
        min_length=1,
        max_length=MAX_LABEL_CHARS,
        description="Free-form type, in the language of the source text.",
    )


class RawFact(BaseModel):
    """One extracted claim, with the evidence that must justify it."""

    model_config = ConfigDict(extra="forbid")

    subject: RawConcept
    predicate: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    object: RawConcept
    object_is_literal: bool = Field(
        default=False,
        description="True when the object is a value (a date, a number, a title) "
        "rather than an entity worth its own node.",
    )
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    quote: str = Field(
        min_length=1,
        max_length=MAX_QUOTE_CHARS,
        description="Verbatim excerpt from the source text supporting this claim.",
    )


class ExtractionResult(BaseModel):
    """The full response for one chunk."""

    model_config = ConfigDict(extra="forbid")

    facts: list[RawFact] = Field(default_factory=list)


def extraction_json_schema() -> dict[str, Any]:
    """Return the JSON schema handed to the backend for constrained decoding."""
    return ExtractionResult.model_json_schema()
