"""Segmentation into extraction-sized chunks.

Two properties matter more than clever boundaries:

1. ``chunk.text == document_text[chunk.char_start:chunk.char_end]``, exactly.
   Everything downstream reports spans relative to the chunk, and provenance
   only survives if that slice identity holds.
2. Chunk boundaries are a pure function of the text. Re-running the pipeline on
   an unchanged document must produce byte-identical chunk ids, or the
   incremental cache is worthless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from diairy.vault.markdown import Block

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n")


@dataclass(frozen=True)
class ChunkSpan:
    """A slice of a document that will be handed to the extraction model."""

    char_start: int
    char_end: int
    text: str
    heading_path: tuple[str, ...]


def _split_oversized(document_text: str, block: Block, target_chars: int) -> list[ChunkSpan]:
    """Split a single block that is too large to fit in one chunk."""
    spans: list[ChunkSpan] = []
    start = block.char_start
    while start < block.char_end:
        hard_end = min(start + target_chars, block.char_end)
        if hard_end < block.char_end:
            window = document_text[start:hard_end]
            boundaries = [match.start() for match in _SENTENCE_END.finditer(window)]
            # Only honour a boundary past the halfway mark, otherwise dense
            # punctuation would produce a stream of tiny chunks.
            usable = [pos for pos in boundaries if pos > target_chars // 2]
            if usable:
                hard_end = start + usable[-1]
        text = document_text[start:hard_end]
        if text.strip():
            spans.append(
                ChunkSpan(
                    char_start=start,
                    char_end=hard_end,
                    text=text,
                    heading_path=block.heading_path,
                )
            )
        start = hard_end
    return spans


def chunk_blocks(
    document_text: str,
    blocks: list[Block],
    *,
    target_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[ChunkSpan]:
    """Group blocks into chunks of roughly ``target_chars``, with overlap.

    Overlap is expressed by letting a chunk start again on a block the previous
    chunk already covered, rather than by copying text around. That keeps the
    slice-identity property intact.
    """
    if target_chars <= 0:
        message = "target_chars must be positive"
        raise ValueError(message)

    chunks: list[ChunkSpan] = []
    index = 0
    total = len(blocks)

    while index < total:
        first = blocks[index]
        start = first.char_start
        end = first.char_end
        last_index = index

        while last_index + 1 < total and (blocks[last_index + 1].char_end - start) <= target_chars:
            last_index += 1
            end = blocks[last_index].char_end

        if last_index == index and (end - start) > target_chars:
            chunks.extend(_split_oversized(document_text, first, target_chars))
            index += 1
            continue

        chunks.append(
            ChunkSpan(
                char_start=start,
                char_end=end,
                text=document_text[start:end],
                heading_path=first.heading_path,
            )
        )

        next_index = last_index + 1
        if overlap_chars > 0 and next_index < total:
            rewind = last_index
            while rewind > index and (end - blocks[rewind].char_start) <= overlap_chars:
                rewind -= 1
            next_index = min(next_index, max(index + 1, rewind + 1))
        index = max(index + 1, next_index)

    return chunks
