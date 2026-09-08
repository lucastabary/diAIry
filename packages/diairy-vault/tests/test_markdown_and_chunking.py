"""Segmentation tests, centred on the offset invariants."""

from __future__ import annotations

from datetime import UTC, datetime

from diairy.vault.chunking import chunk_blocks
from diairy.vault.markdown import infer_event_time, parse_document, split_blocks

DOCUMENT = """---
date: 2026-03-14
mood: distrait
---

# Journée

Relu trois pages de Borges ce matin.

## Travail

Le parser Markdown m'a pris deux heures.
Toujours pas satisfait des offsets.

## Idées

Et si le graphe gardait les versions ?
"""

FALLBACK = datetime(2026, 9, 8, tzinfo=UTC)


def test_frontmatter_is_parsed_and_the_body_offset_is_exact() -> None:
    parsed = parse_document(DOCUMENT)
    assert parsed.frontmatter["mood"] == "distrait"
    assert DOCUMENT[parsed.body_offset :] == parsed.body


def test_document_without_frontmatter_starts_at_zero() -> None:
    parsed = parse_document("Juste du texte.")
    assert parsed.frontmatter == {}
    assert parsed.body_offset == 0


def test_blocks_report_absolute_offsets_into_the_original_file() -> None:
    parsed = parse_document(DOCUMENT)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    assert blocks
    for block in blocks:
        assert DOCUMENT[block.char_start : block.char_end] == block.text


def test_blocks_carry_their_heading_path() -> None:
    parsed = parse_document(DOCUMENT)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    paths = {block.text[:20]: block.heading_path for block in blocks}
    assert paths["Relu trois pages de "] == ("Journée",)
    assert paths["Le parser Markdown m"] == ("Journée", "Travail")


def test_chunks_slice_back_to_the_document_exactly() -> None:
    """The invariant the whole provenance chain rests on."""
    parsed = parse_document(DOCUMENT)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    chunks = chunk_blocks(DOCUMENT, blocks, target_chars=120, overlap_chars=20)
    assert chunks
    for chunk in chunks:
        assert DOCUMENT[chunk.char_start : chunk.char_end] == chunk.text


def test_chunking_is_deterministic() -> None:
    parsed = parse_document(DOCUMENT)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    first = chunk_blocks(DOCUMENT, blocks, target_chars=120, overlap_chars=20)
    second = chunk_blocks(DOCUMENT, blocks, target_chars=120, overlap_chars=20)
    assert first == second


def test_chunking_terminates_and_covers_every_block() -> None:
    parsed = parse_document(DOCUMENT)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    chunks = chunk_blocks(DOCUMENT, blocks, target_chars=60, overlap_chars=30)
    covered = "".join(chunk.text for chunk in chunks)
    for block in blocks:
        assert block.text.split("\n")[0] in covered


def test_an_oversized_block_is_split_rather_than_dropped() -> None:
    long_text = " ".join(f"Phrase numero {index}." for index in range(200))
    blocks = split_blocks(long_text)
    chunks = chunk_blocks(long_text, blocks, target_chars=200, overlap_chars=0)
    assert len(chunks) > 1
    for chunk in chunks:
        assert long_text[chunk.char_start : chunk.char_end] == chunk.text
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(long_text)


def test_empty_document_yields_no_chunks() -> None:
    assert chunk_blocks("", [], target_chars=100) == []


def test_event_time_comes_from_frontmatter_first() -> None:
    parsed = parse_document(DOCUMENT)
    assert infer_event_time("note.md", parsed.frontmatter, FALLBACK).date().isoformat() == (
        "2026-03-14"
    )


def test_event_time_falls_back_to_the_filename() -> None:
    resolved = infer_event_time("journal/2025-12-24-veille.md", {}, FALLBACK)
    assert resolved.date().isoformat() == "2025-12-24"


def test_event_time_falls_back_to_the_caller_when_nothing_is_known() -> None:
    assert infer_event_time("notes.md", {}, FALLBACK) == FALLBACK


def test_impossible_date_in_filename_is_ignored() -> None:
    assert infer_event_time("2026-13-45.md", {}, FALLBACK) == FALLBACK
