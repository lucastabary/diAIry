"""Deterministic identifiers.

No identifier in diAIry is random except a run id. Everything else is derived
from content, so that re-ingesting an unchanged vault is a no-op and
reprocessing an unchanged chunk produces the exact same fact ids.
"""

from __future__ import annotations

import uuid

from diairy.core.hashing import digest_of


def normalize_relative_path(relative_path: str) -> str:
    """Normalise a vault-relative path so identifiers survive OS differences."""
    return relative_path.replace("\\", "/").strip("/")


def document_id(relative_path: str) -> str:
    """Identify a document by its stable location inside the vault."""
    return digest_of(["document", normalize_relative_path(relative_path)])


def document_version_id(doc_id: str, content_hash: str) -> str:
    """Identify one revision of a document by its content."""
    return digest_of(["document_version", doc_id, content_hash])


def chunk_id(version_id: str, char_start: int, char_end: int) -> str:
    """Identify a chunk by its exact span inside a document version."""
    return digest_of(["chunk", version_id, str(char_start), str(char_end)])


def fact_id(
    *,
    chunk: str,
    subject: str,
    predicate: str,
    obj: str,
    char_start: int,
    char_end: int,
) -> str:
    """Identify a fact by its content *and* by the evidence that produced it.

    Two identical claims supported by two different sentences are two facts:
    that is deliberate, because provenance is part of the claim.
    """
    return digest_of(["fact", chunk, subject, predicate, obj, str(char_start), str(char_end)])


def concept_id(canonical_label: str, canonical_type: str) -> str:
    """Identify a canonical concept by its normalised label and type."""
    return digest_of(["concept", canonical_type, canonical_label])


def new_run_id() -> str:
    """Return a fresh identifier for a processing run.

    Runs are events, not content, so this is the one place we use randomness.
    """
    return uuid.uuid4().hex
