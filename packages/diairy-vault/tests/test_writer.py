"""Tests for adding new source entries to the vault.

The contract that matters: a new entry only ever *adds* a file, its frontmatter
date round-trips to what the caller set, and an existing note is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from diairy.core.errors import VaultError
from diairy.vault.markdown import infer_event_time, parse_document
from diairy.vault.writer import write_attachment, write_entry


def _read(entry_path: Path) -> str:
    return entry_path.read_text(encoding="utf-8")


def test_writing_an_entry_creates_a_dated_markdown_file(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    entry = write_entry(tmp_path, body="J'ai adore Lisbonne.", event_time=when, title="Voyage")

    assert entry.absolute_path.exists()
    assert entry.relative_path == "2026-04-02-voyage.md"
    assert "J'ai adore Lisbonne." in _read(entry.absolute_path)


def test_the_frontmatter_date_round_trips_to_the_event_time(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    entry = write_entry(tmp_path, body="Une note.", event_time=when)

    parsed = parse_document(_read(entry.absolute_path))
    inferred = infer_event_time(entry.relative_path, parsed.frontmatter, fallback=datetime.now(UTC))
    assert inferred == when


def test_a_time_of_day_is_preserved_as_a_datetime(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, 9, 30, tzinfo=UTC)
    entry = write_entry(tmp_path, body="Cafe du matin.", event_time=when)

    parsed = parse_document(_read(entry.absolute_path))
    assert "datetime:" in _read(entry.absolute_path)
    inferred = infer_event_time(entry.relative_path, parsed.frontmatter, fallback=datetime.now(UTC))
    assert inferred == when


def test_a_second_entry_the_same_day_never_overwrites_the_first(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    first = write_entry(tmp_path, body="First.", event_time=when, title="Voyage")
    second = write_entry(tmp_path, body="Second.", event_time=when, title="Voyage")

    assert first.absolute_path != second.absolute_path
    assert "First." in _read(first.absolute_path)
    assert "Second." in _read(second.absolute_path)
    assert second.relative_path == "2026-04-02-voyage-2.md"


def test_a_titleless_entry_is_named_from_its_first_line(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    entry = write_entry(tmp_path, body="# Marathon\n\nFini en 3h58.", event_time=when)
    assert entry.relative_path == "2026-04-02-marathon.md"


def test_an_empty_body_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VaultError):
        write_entry(tmp_path, body="   \n\n", event_time=datetime(2026, 4, 2, tzinfo=UTC))


def test_the_vault_directory_is_created_if_missing(tmp_path: Path) -> None:
    vault = tmp_path / "not" / "there" / "yet"
    entry = write_entry(vault, body="Une idee.", event_time=datetime(2026, 4, 2, tzinfo=UTC))
    assert entry.absolute_path.exists()
    assert entry.absolute_path.parent == vault


def test_an_unusable_title_falls_back_to_the_body_then_the_bare_date(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    # A punctuation-only title yields no slug, so the first body line names it.
    from_body = write_entry(tmp_path, body="Contenu utile.", event_time=when, title="!!!")
    assert from_body.relative_path == "2026-04-02-contenu-utile.md"

    # When neither title nor body yields a slug, the bare date stands alone.
    bare = write_entry(tmp_path, body="!!! ??? ...", event_time=when, title="???")
    assert bare.relative_path == "2026-04-02.md"


def test_an_audio_link_is_recorded_in_the_frontmatter(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, tzinfo=UTC)
    entry = write_entry(
        tmp_path,
        body="Note dictee.",
        event_time=when,
        audio="attachments/2026-04-02-090000-abcd1234.webm",
    )
    parsed = parse_document(_read(entry.absolute_path))
    assert parsed.frontmatter["audio"] == "attachments/2026-04-02-090000-abcd1234.webm"


def test_a_recording_is_written_under_attachments(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, 9, tzinfo=UTC)
    attachment = write_attachment(tmp_path, b"fake-audio-bytes", event_time=when, suffix="webm")

    assert attachment.absolute_path.exists()
    assert attachment.absolute_path.read_bytes() == b"fake-audio-bytes"
    assert attachment.relative_path.startswith("attachments/")
    assert attachment.relative_path.endswith(".webm")


def test_a_leading_dot_in_the_suffix_is_tolerated(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, 9, tzinfo=UTC)
    attachment = write_attachment(tmp_path, b"x", event_time=when, suffix=".WAV")
    assert attachment.relative_path.endswith(".wav")


def test_two_different_recordings_never_collide(tmp_path: Path) -> None:
    when = datetime(2026, 4, 2, 9, tzinfo=UTC)
    first = write_attachment(tmp_path, b"one", event_time=when, suffix="webm")
    second = write_attachment(tmp_path, b"two", event_time=when, suffix="webm")
    assert first.absolute_path != second.absolute_path
    assert first.absolute_path.read_bytes() == b"one"
    assert second.absolute_path.read_bytes() == b"two"


def test_an_empty_recording_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VaultError):
        write_attachment(tmp_path, b"", event_time=datetime(2026, 4, 2, tzinfo=UTC), suffix="webm")
