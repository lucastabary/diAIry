"""Reading the vault, and keeping its history in git."""

from __future__ import annotations

from pathlib import Path

import pytest

from diairy.core.errors import VaultError
from diairy.vault.git import VaultGit
from diairy.vault.scanner import Vault


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_iter_files_is_sorted_and_hashes_content(vault_dir: Path) -> None:
    _write(vault_dir, "b.md", "deuxieme")
    _write(vault_dir, "a.md", "premier")
    files = list(Vault(vault_dir).iter_files())
    assert [file.relative_path for file in files] == ["a.md", "b.md"]
    assert files[0].content == "premier"
    assert files[0].content_hash != files[1].content_hash


def test_nested_paths_use_forward_slashes(vault_dir: Path) -> None:
    _write(vault_dir, "2026/03/14.md", "note")
    (file,) = Vault(vault_dir).iter_files()
    assert file.relative_path == "2026/03/14.md"


def test_hidden_tool_directories_are_skipped(vault_dir: Path) -> None:
    _write(vault_dir, "keep.md", "oui")
    _write(vault_dir, ".obsidian/plugins/note.md", "non")
    assert [file.relative_path for file in Vault(vault_dir).iter_files()] == ["keep.md"]


def test_non_markdown_files_are_ignored(vault_dir: Path) -> None:
    _write(vault_dir, "note.md", "oui")
    _write(vault_dir, "photo.png", "non")
    assert [file.relative_path for file in Vault(vault_dir).iter_files()] == ["note.md"]


def test_byte_order_mark_is_stripped(vault_dir: Path) -> None:
    (vault_dir / "bom.md").write_bytes(b"\xef\xbb\xbfBonjour")
    (file,) = Vault(vault_dir).iter_files()
    assert file.content == "Bonjour"


def test_missing_vault_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(VaultError, match="diairy init"):
        list(Vault(tmp_path / "absent").iter_files())


def test_non_utf8_file_is_reported_not_mangled(vault_dir: Path) -> None:
    (vault_dir / "latin.md").write_bytes(b"caf\xe9")
    with pytest.raises(VaultError, match="UTF-8"):
        list(Vault(vault_dir).iter_files())


def test_git_history_records_every_snapshot(vault_dir: Path) -> None:
    git = VaultGit(vault_dir)
    git.init()
    assert git.is_repository()
    assert git.head_commit() is None

    _write(vault_dir, "note.md", "premiere version")
    first = git.commit_all("diairy: snapshot 1")
    assert first is not None

    _write(vault_dir, "note.md", "version corrigee")
    second = git.commit_all("diairy: snapshot 2")
    assert second is not None
    assert second != first

    # The point of all this: the old text is still reachable.
    assert git.file_at_commit(first, "note.md") == "premiere version"
    assert git.file_at_commit(second, "note.md") == "version corrigee"


def test_committing_an_unchanged_vault_does_nothing(vault_dir: Path) -> None:
    git = VaultGit(vault_dir)
    git.init()
    _write(vault_dir, "note.md", "stable")
    git.commit_all("diairy: snapshot")
    assert git.commit_all("diairy: snapshot again") is None


def test_init_is_idempotent(vault_dir: Path) -> None:
    git = VaultGit(vault_dir)
    git.init()
    git.init()
    assert git.is_repository()
