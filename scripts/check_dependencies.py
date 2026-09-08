"""Refuse dependencies nobody has reviewed.

diAIry's promise is that nothing leaves the machine. Every third-party package
is a chance for that to stop being true -- an analytics call in a post-install
hook, a crash reporter, an "anonymous usage statistic". Reviewing each one is
cheap; discovering one after the fact is not.

So the allowlist below is the complete set of packages we ship, and CI fails on
anything else. Adding an entry is a deliberate decision with a reason attached,
which is exactly the friction we want.

Run: python scripts/check_dependencies.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALLOWED: dict[str, str] = {
    # -- runtime --------------------------------------------------------------
    "httpx": "HTTP client for the local model server. Loopback only.",
    "kuzu": "Embedded graph database. No server, no network.",
    "numpy": "Vector maths for canonicalisation.",
    "platformdirs": "OS-correct config and data directories.",
    "pydantic": "Domain model validation and the extraction JSON schema.",
    "pydantic-settings": "Layered configuration.",
    "pyyaml": "Markdown frontmatter parsing.",
    "rich": "CLI output.",
    "sqlite-vec": "Vector search inside the existing SQLite file.",
    "typer": "CLI argument parsing.",
    # -- development ----------------------------------------------------------
    "mypy": "Type checking.",
    "pytest": "Test runner.",
    "pytest-cov": "Coverage measurement.",
    "ruff": "Linting and formatting.",
    "types-pyyaml": "Type stubs for pyyaml.",
    # -- workspace members ----------------------------------------------------
    "diairy-cli": "Workspace member.",
    "diairy-core": "Workspace member.",
    "diairy-nlp": "Workspace member.",
    "diairy-pipeline": "Workspace member.",
    "diairy-query": "Workspace member.",
    "diairy-store": "Workspace member.",
    "diairy-vault": "Workspace member.",
}

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def requirement_name(requirement: str) -> str:
    """Extract the bare package name from a PEP 508 requirement string."""
    match = _REQUIREMENT_NAME.match(requirement.strip())
    return match.group(0).lower().replace("_", "-") if match else ""


def declared_dependencies() -> dict[str, set[Path]]:
    """Every dependency declared anywhere in the workspace, and where."""
    found: dict[str, set[Path]] = {}
    manifests = [ROOT / "pyproject.toml", *sorted(ROOT.glob("packages/*/pyproject.toml"))]
    for manifest in manifests:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        requirements: list[str] = list(data.get("project", {}).get("dependencies", []))
        for group in data.get("dependency-groups", {}).values():
            requirements.extend(item for item in group if isinstance(item, str))
        for requirement in requirements:
            name = requirement_name(requirement)
            if name:
                found.setdefault(name, set()).add(manifest.relative_to(ROOT))
    return found


def main() -> int:
    unreviewed = {
        name: sources
        for name, sources in sorted(declared_dependencies().items())
        if name not in ALLOWED
    }
    if not unreviewed:
        print(f"OK: {len(declared_dependencies())} declared dependencies, all reviewed.")
        return 0

    print("Unreviewed dependencies found.\n")
    for name, sources in unreviewed.items():
        locations = ", ".join(str(path) for path in sorted(sources))
        print(f"  {name}  (declared in {locations})")
    print(
        "\nEvery dependency is reviewed before it ships: it is one more thing that "
        "could reach the network.\nIf this one is genuinely needed, add it to ALLOWED "
        "in scripts/check_dependencies.py with a one-line reason,\nand say so in the "
        "pull request."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
