"""Stop journal content from ever reaching the repository.

The one mistake in this project that cannot be undone is committing a real
journal entry: git remembers, and the remote may already have it. This hook is
the last line of defence before that happens.

It runs on staged files only, and it is deliberately blunt. A false positive
costs a rename; a false negative costs a rewritten history and a rotated repo.

Run: python scripts/check_no_personal_data.py <file>...
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".gguf",
    ".safetensors",
    ".diairy",
}

FORBIDDEN_PATH_PARTS = {"vault", "vaults", "journal", "runs", "weights"}

ALLOWED_PREFIXES = (
    "packages/",  # source code, including packages/diairy-vault/...
    "evals/fixtures/",  # synthetic French fixtures, committed on purpose
    "scripts/",
    "docs/",
)


def is_allowed(path: Path) -> bool:
    posix = path.as_posix()
    return any(posix.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def problems(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        posix = path.as_posix()
        if path.suffix in FORBIDDEN_SUFFIXES:
            found.append(f"{posix}: derived store or model weights are never committed")
            continue
        if posix.endswith((".jsonl", ".jsonl.gz")) and not is_allowed(path):
            found.append(f"{posix}: run logs contain verbatim journal text")
            continue
        if is_allowed(path):
            continue
        parts = {part.lower() for part in path.parts[:-1]}
        overlap = parts & FORBIDDEN_PATH_PARTS
        if overlap:
            found.append(f"{posix}: lives under {'/'.join(sorted(overlap))}/")
    return found


def main(argv: list[str]) -> int:
    found = problems([Path(arg) for arg in argv])
    if not found:
        return 0
    print("Refusing to commit what looks like personal data:\n")
    for problem in found:
        print(f"  {problem}")
    print(
        "\nThe vault belongs outside the repository. Test fixtures must be synthetic.\n"
        "If this is a false positive, adjust scripts/check_no_personal_data.py --\n"
        "never bypass the hook."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
