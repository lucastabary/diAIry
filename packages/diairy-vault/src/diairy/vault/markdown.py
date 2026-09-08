"""Markdown parsing that never loses a character offset.

Every span this module reports is an absolute offset into the *original* file
text, frontmatter included. That is what lets a fact extracted three stages
later point back at the exact characters that justify it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import yaml

from diairy.core.clock import ensure_aware
from diairy.core.errors import VaultError

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

_EVENT_TIME_KEYS = ("date", "datetime", "created", "created_at", "timestamp")


@dataclass(frozen=True)
class Block:
    """A paragraph-sized unit of a document, with absolute offsets."""

    char_start: int
    char_end: int
    text: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True)
class ParsedDocument:
    """A document split into its frontmatter and its body."""

    frontmatter: dict[str, Any]
    body: str
    body_offset: int
    """Absolute offset at which ``body`` starts in the original text."""


def parse_document(text: str) -> ParsedDocument:
    """Split YAML frontmatter from the body, keeping the body's offset."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return ParsedDocument(frontmatter={}, body=text, body_offset=0)
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise VaultError(f"Invalid YAML frontmatter: {exc}") from exc
    frontmatter = loaded if isinstance(loaded, dict) else {}
    return ParsedDocument(
        frontmatter=frontmatter, body=text[match.end() :], body_offset=match.end()
    )


def split_blocks(text: str, *, offset: int = 0) -> list[Block]:
    """Split ``text`` into blocks separated by blank lines and headings.

    ``offset`` is added to every reported position, so callers can pass a body
    and still get offsets into the whole file.
    """
    blocks: list[Block] = []
    heading_path: list[str] = []
    current: list[str] = []
    current_start = 0
    position = 0

    def flush() -> None:
        if not current:
            return
        raw = "".join(current)
        stripped = raw.rstrip()
        if stripped:
            blocks.append(
                Block(
                    char_start=offset + current_start,
                    char_end=offset + current_start + len(stripped),
                    text=stripped,
                    heading_path=tuple(heading_path),
                )
            )
        current.clear()

    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        heading = _HEADING.match(bare)
        if heading is not None:
            flush()
            level = len(heading.group(1))
            del heading_path[level - 1 :]
            heading_path.append(heading.group(2))
            position += len(line)
            current_start = position
            continue
        if not bare.strip():
            flush()
            position += len(line)
            current_start = position
            continue
        if not current:
            current_start = position
        current.append(line)
        position += len(line)

    flush()
    return blocks


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            return ensure_aware(datetime.fromisoformat(value))
        except ValueError:
            match = _DATE_IN_NAME.search(value)
            if match is None:
                return None
            year, month, day = (int(part) for part in match.groups())
            return datetime(year, month, day, tzinfo=UTC)
    return None


def infer_event_time(
    relative_path: str,
    frontmatter: dict[str, Any],
    fallback: datetime,
) -> datetime:
    """Work out when the entry was written.

    Frontmatter wins, then a ``YYYY-MM-DD`` in the filename, then the caller's
    fallback. Zero-friction capture means most notes carry no metadata at all,
    so a good filename heuristic matters more than it looks.
    """
    for key in _EVENT_TIME_KEYS:
        if key in frontmatter:
            resolved = _coerce_datetime(frontmatter[key])
            if resolved is not None:
                return resolved
    match = _DATE_IN_NAME.search(relative_path)
    if match is not None:
        year, month, day = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            pass
    return ensure_aware(fallback)
