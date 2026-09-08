"""Content addressing.

Every derived artefact in diAIry is keyed by the hash of what produced it. That
is what makes the pipeline incremental (only re-run stages whose inputs moved)
and reprocessing idempotent (the same input yields the same identifier, so a
rebuild does not duplicate anything).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

_DIGEST_CHARS = 32
"""Truncation length for identifiers: 128 bits, ample against collisions here."""


def sha256_text(value: str) -> str:
    """Return the hex SHA-256 of ``value`` encoded as UTF-8."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Return the hex SHA-256 of ``value``."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Return the hex SHA-256 of a file, read incrementally."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def digest_of(parts: Iterable[str], *, length: int = _DIGEST_CHARS) -> str:
    """Return a short, stable digest of an ordered sequence of strings.

    Parts are joined with a NUL separator so that ``["ab", "c"]`` and
    ``["a", "bc"]`` cannot collide.
    """
    return sha256_text("\0".join(parts))[:length]
