"""Raw Markdown sources: reading, history and deterministic segmentation."""

from diairy.vault.chunking import ChunkSpan, chunk_blocks
from diairy.vault.git import VaultGit
from diairy.vault.markdown import (
    Block,
    ParsedDocument,
    infer_event_time,
    parse_document,
    split_blocks,
)
from diairy.vault.scanner import DEFAULT_PATTERNS, Vault, VaultFile

__all__ = [
    "DEFAULT_PATTERNS",
    "Block",
    "ChunkSpan",
    "ParsedDocument",
    "Vault",
    "VaultFile",
    "VaultGit",
    "chunk_blocks",
    "infer_event_time",
    "parse_document",
    "split_blocks",
]
