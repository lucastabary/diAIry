"""Adding a new source entry to the vault.

The scanner treats the vault as read-only on purpose. This module is the one
deliberate exception, and it is deliberately narrow: it creates a *new* file and
never touches an existing one. That keeps both load-bearing invariants intact --
"the vault is the only source of truth" (we are adding source, not deriving it)
and "nothing is deleted" (a new entry only adds characters that did not exist
before; it can never overwrite or shorten another).

Everything else about a note -- how it is segmented, when it happened, what it
claims -- is still worked out downstream from the file this writes. All we do
here is put well-formed Markdown on disk, with a frontmatter date the rest of
the pipeline already knows how to read.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from diairy.core.clock import ensure_aware
from diairy.core.errors import VaultError
from diairy.core.ids import normalize_relative_path

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_NON_EXT = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_CHARS = 48
_MAX_COLLISION_SUFFIX = 1000
"""Give up rather than spin forever if a thousand names are somehow taken."""

_ATTACHMENTS_DIR = "attachments"
"""Where non-Markdown source (recordings, later images) lives in the vault.

The scanner ingests ``*.md`` only, so files here are never mistaken for source;
they are provenance, linked from an entry's frontmatter. See ADR 0009.
"""


@dataclass(frozen=True)
class WrittenEntry:
    """The file a :func:`write_entry` call created."""

    relative_path: str
    absolute_path: Path
    event_time: datetime


@dataclass(frozen=True)
class WrittenAttachment:
    """The binary file a :func:`write_attachment` call created."""

    relative_path: str
    absolute_path: Path


def _slugify(text: str) -> str:
    """A filesystem- and cross-platform-safe stem fragment, or ``""``.

    Accents are folded to ASCII so a French title yields a plain filename that
    behaves the same on every machine the vault is synced to.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", folded.lower()).strip("-")
    return slug[:_MAX_SLUG_CHARS].strip("-")


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _render(title: str | None, event_time: datetime, body: str, audio: str | None) -> str:
    """Frontmatter (date, optional title, optional audio) then the body, verbatim.

    A midnight event time is written as a bare ``date``; any other time keeps its
    full precision as ``datetime``. Both keys are ones ``infer_event_time``
    already honours, so the entry round-trips to exactly the time set here.

    ``audio``, when present, is the vault-relative path of the recording this
    transcript came from, kept as provenance (ADR 0009).
    """
    lines = ["---"]
    if event_time.hour or event_time.minute or event_time.second:
        lines.append(f"datetime: {event_time.isoformat()}")
    else:
        lines.append(f"date: {event_time.date().isoformat()}")
    if title:
        # JSON string syntax is valid YAML double-quoted scalar syntax, so this
        # safely quotes any colon, quote or newline without a YAML dependency.
        lines.append(f"title: {json.dumps(title, ensure_ascii=False)}")
    if audio:
        lines.append(f"audio: {json.dumps(audio, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip("\n") + "\n"


def _unique_path(directory: Path, stem: str, *, extension: str = "md") -> Path:
    """A path under ``directory`` for ``stem.extension`` that does not exist yet.

    Never returns the path of an existing file: colliding with one and letting
    the caller overwrite it would destroy source, which must never happen.
    """
    candidate = directory / f"{stem}.{extension}"
    if not candidate.exists():
        return candidate
    for suffix in range(2, _MAX_COLLISION_SUFFIX + 1):
        candidate = directory / f"{stem}-{suffix}.{extension}"
        if not candidate.exists():
            return candidate
    message = (
        f"Could not find a free filename for {stem!r} in {directory}. "
        f"Give the entry a more specific title."
    )
    raise VaultError(message)


def write_entry(
    vault_root: Path,
    *,
    body: str,
    event_time: datetime,
    title: str | None = None,
    audio: str | None = None,
) -> WrittenEntry:
    """Write a new Markdown entry into the vault and return where it landed.

    Args:
        vault_root: the vault directory. Created if it does not exist, because
            asking to save an entry is an explicit instruction to write here.
        body: the source text, in the user's own words. Stored verbatim.
        event_time: when the entry was written; becomes the frontmatter date.
        title: an optional heading, used both in frontmatter and to name the file.
        audio: an optional vault-relative path to the recording this entry was
            transcribed from, recorded in the frontmatter as provenance.

    The filename is ``YYYY-MM-DD`` plus a slug of the title (or the first line of
    the body), which also lets the date survive even if the frontmatter is later
    stripped. A name clash never overwrites: a numeric suffix is added instead.
    """
    if not body.strip():
        raise VaultError("There is nothing to save. Write the entry first, then save it.")

    when = ensure_aware(event_time)
    slug = _slugify(title or "") or _slugify(_first_line(body))
    stem = when.date().isoformat() if not slug else f"{when.date().isoformat()}-{slug}"

    vault_root.mkdir(parents=True, exist_ok=True)
    path = _unique_path(vault_root, stem)
    try:
        path.write_text(_render(title, when, body, audio), encoding="utf-8")
    except OSError as exc:
        raise VaultError(f"Could not write the entry to {path}: {exc}") from exc

    return WrittenEntry(
        relative_path=normalize_relative_path(str(path.relative_to(vault_root))),
        absolute_path=path,
        event_time=when,
    )


def _sanitise_extension(suffix: str) -> str:
    """A safe, lowercase file extension without the dot (e.g. ``webm``, ``wav``)."""
    cleaned = _NON_EXT.sub("", suffix.lstrip(".").lower())
    return cleaned or "bin"


def write_attachment(
    vault_root: Path,
    data: bytes,
    *,
    event_time: datetime,
    suffix: str,
) -> WrittenAttachment:
    """Write a binary attachment (a recording) into the vault, additively.

    The file lands under ``attachments/`` with a timestamped, content-addressed
    name, so two recordings made in the same second never collide and an
    identical re-upload is obvious. Like :func:`write_entry`, this only ever
    creates a new file; it never overwrites one.

    Args:
        vault_root: the vault directory. Created if absent.
        data: the raw bytes of the recording.
        event_time: when it was captured; dates the filename.
        suffix: the source file extension (``"webm"``, ``".wav"``), sanitised.

    Returns the vault-relative path to store in an entry's ``audio`` frontmatter.
    """
    if not data:
        raise VaultError("There is nothing to save: the recording is empty.")

    when = ensure_aware(event_time)
    digest = hashlib.sha256(data).hexdigest()[:8]
    extension = _sanitise_extension(suffix)
    stem = f"{when.strftime('%Y-%m-%d-%H%M%S')}-{digest}"

    directory = vault_root / _ATTACHMENTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = _unique_path(directory, stem, extension=extension)
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise VaultError(f"Could not write the recording to {path}: {exc}") from exc

    return WrittenAttachment(
        relative_path=normalize_relative_path(str(path.relative_to(vault_root))),
        absolute_path=path,
    )
