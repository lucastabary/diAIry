"""The domain model.

Read this file first: the whole system is an elaborate way of turning the
Markdown in ``Document`` into the ``Fact`` rows at the bottom, without ever
losing the thread back to the original characters.

Two invariants are encoded here and enforced everywhere else:

* **Nothing is deleted.** A fact that stops being true gets ``superseded_by``
  set; the row itself stays forever.
* **Everything is bitemporal.** ``event_time`` is when the thing happened,
  ``knowledge_time`` is when we learned it. Asking "what did I think of this
  book *when I read it*" needs both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LITERAL_TYPE = "literal"
"""Reserved object type marking a value rather than an entity."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Document(_Frozen):
    """A file in the vault, tracked across all of its revisions."""

    id: str
    relative_path: str
    first_seen_at: datetime


class DocumentVersion(_Frozen):
    """One revision of a document, identified by the hash of its content."""

    id: str
    document_id: str
    content_hash: str
    byte_size: int
    ingested_at: datetime
    event_time: datetime
    """When the entry was written, from frontmatter or the filename.

    Distinct from ``ingested_at``: a note written on holiday and synced a week
    later must land on the day it was written, not the day we read it.
    """
    git_commit: str | None = None


class Chunk(_Frozen):
    """A contiguous span of a document version, the unit of extraction.

    ``text`` is exactly ``document_text[char_start:char_end]``. Downstream code
    relies on that identity to translate a span inside a chunk back into a span
    inside the file.
    """

    id: str
    document_version_id: str
    ordinal: int
    char_start: int
    char_end: int
    text: str
    heading_path: tuple[str, ...] = ()
    """Enclosing Markdown headings, outermost first. Context for the model."""


class ConceptRef(_Frozen):
    """A reference to a concept, in the model's own words.

    Both ``label`` and ``type`` are free-form: the ontology is open. The raw
    strings are kept verbatim forever; canonicalisation happens downstream and
    is always a *view* over these, never a replacement.
    """

    label: str
    type: str


class Evidence(_Frozen):
    """Where in the source a fact came from, down to the character.

    Offsets are absolute within the document version, not relative to the chunk,
    so a fact can be highlighted in the original file without re-deriving the
    segmentation that produced it.
    """

    chunk_id: str
    char_start: int
    char_end: int
    quote: str


class Fact(_Frozen):
    """A single claim extracted from the journal, with its provenance."""

    id: str
    subject: ConceptRef
    predicate: str
    object: ConceptRef
    object_is_literal: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: Evidence
    run_id: str
    event_time: datetime
    knowledge_time: datetime
    superseded_by: str | None = None

    @property
    def is_current(self) -> bool:
        """Whether this fact is part of the present state of knowledge."""
        return self.superseded_by is None


RunStatus = Literal["running", "completed", "failed"]


class Run(_Frozen):
    """One processing session, logged in full so any fact can be explained.

    The raw prompts and responses are written next to this row as gzipped JSONL;
    this record holds the metadata needed to find and interpret them.
    """

    id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = "running"
    model: str = ""
    profile: str = ""
    prompt_version: str = ""
    pipeline_version: str = ""
    stats: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class CanonicalConcept(_Frozen):
    """A merged concept: many raw labels pointing at one thing."""

    id: str
    label: str
    type: str
    occurrence_count: int = 0
    promoted: bool = False


class Alias(_Frozen):
    """A raw label resolved onto a canonical concept, and how we decided."""

    raw_label: str
    raw_type: str
    canonical_id: str
    method: Literal["exact", "normalized", "embedding", "manual"]
    score: float = Field(ge=0.0, le=1.0, default=1.0)
