"""Reading the vault.

The vault is a plain directory of Markdown that the user owns and can edit in
any tool. We only ever read it. The single write is the git commit that records
what changed, which adds history without touching content.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from diairy.core.errors import VaultError
from diairy.core.hashing import sha256_bytes
from diairy.core.ids import document_id, normalize_relative_path

DEFAULT_PATTERNS = ("*.md", "*.markdown")
DEFAULT_EXCLUDED_DIRS = frozenset({".git", ".obsidian", ".diairy", ".trash", "node_modules"})


@dataclass(frozen=True)
class VaultFile:
    """A source file, read and hashed."""

    relative_path: str
    absolute_path: Path
    content: str
    content_hash: str
    byte_size: int

    @property
    def document_id(self) -> str:
        return document_id(self.relative_path)


class Vault:
    """A directory of raw Markdown, read-only apart from git bookkeeping."""

    def __init__(
        self,
        root: Path,
        *,
        patterns: tuple[str, ...] = DEFAULT_PATTERNS,
        excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
    ) -> None:
        self.root = root
        self.patterns = patterns
        self.excluded_dirs = excluded_dirs

    def exists(self) -> bool:
        return self.root.is_dir()

    def _is_excluded(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        return any(part in self.excluded_dirs for part in relative.parts)

    def iter_paths(self) -> Iterator[Path]:
        """Yield every source file in the vault."""
        if not self.exists():
            message = (
                f"The vault directory {self.root} does not exist. "
                f"Run diairy init, or point DIAIRY_VAULT_PATH at an existing directory."
            )
            raise VaultError(message)
        seen: set[Path] = set()
        for pattern in self.patterns:
            for path in self.root.rglob(pattern):
                if path.is_file() and not self._is_excluded(path) and path not in seen:
                    seen.add(path)
                    yield path

    def read(self, path: Path) -> VaultFile:
        """Read and hash one file."""
        try:
            raw = path.read_bytes()
        except OSError as exc:
            message = f"Could not read {path}: {exc}"
            raise VaultError(message) from exc
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            message = f"{path} is not valid UTF-8. diAIry only reads UTF-8 text files."
            raise VaultError(message) from exc
        return VaultFile(
            relative_path=normalize_relative_path(str(path.relative_to(self.root))),
            absolute_path=path,
            content=content,
            content_hash=sha256_bytes(raw),
            byte_size=len(raw),
        )

    def iter_files(self) -> Iterator[VaultFile]:
        """Yield every source file, read and hashed, in a deterministic order.

        rglob order is filesystem-dependent; sorting keeps runs reproducible
        across machines, which the incremental cache relies on.
        """
        for path in sorted(self.iter_paths()):
            yield self.read(path)
