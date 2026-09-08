"""Git-backed history for the vault.

The vault is the only irreplaceable data in the system, and the user asked for
a strict no-deletion policy. Rather than reinvent versioning, we let git do it:
the vault is a repository, every ingestion run commits whatever changed, and
each document version records the commit it was read at.

That buys us, for free: full history, diffs between revisions, and the ability
to reconstruct the vault exactly as it was on any past date -- which is what
time-travel queries need.

This is a thin subprocess wrapper on purpose. It keeps the dependency surface
at zero and behaves identically on Windows and macOS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from diairy.core.errors import VaultError

_GIT_TIMEOUT_SECONDS = 120


class VaultGit:
    """Git operations scoped to a vault directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            # S603/S607: fixed argument list, no shell, no user-controlled binary.
            return subprocess.run(  # noqa: S603
                ["git", *args],  # noqa: S607 -- git is resolved from PATH by design
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=check,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise VaultError(
                "git was not found on PATH. diAIry uses git to keep the full "
                "history of the vault; install it and try again."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise VaultError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
        except subprocess.TimeoutExpired as exc:
            raise VaultError(f"git {' '.join(args)} timed out") from exc

    def is_repository(self) -> bool:
        """Whether the vault root is already a git working tree."""
        if not (self.root / ".git").exists():
            return False
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def init(self) -> None:
        """Initialise the vault as a git repository if it is not one already."""
        if self.is_repository():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "--initial-branch=main")

    def head_commit(self) -> str | None:
        """Return the current HEAD sha, or ``None`` on an empty repository."""
        result = self._run("rev-parse", "HEAD", check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def is_dirty(self) -> bool:
        """Whether the working tree has uncommitted changes."""
        result = self._run("status", "--porcelain")
        return bool(result.stdout.strip())

    def commit_all(self, message: str) -> str | None:
        """Commit every change in the vault. Returns the new sha, or ``None``.

        ``None`` means there was nothing to commit, which is the common case on
        a re-run and is not an error.
        """
        if not self.is_dirty():
            return None
        self._run("add", "--all")
        self._run(
            "-c",
            "user.name=diAIry",
            "-c",
            "user.email=diairy@localhost",
            "commit",
            "--no-gpg-sign",
            "--message",
            message,
        )
        return self.head_commit()

    def file_at_commit(self, commit: str, relative_path: str) -> str:
        """Read a file as it was at a given commit. The time machine."""
        result = self._run("show", f"{commit}:{relative_path}")
        return result.stdout
