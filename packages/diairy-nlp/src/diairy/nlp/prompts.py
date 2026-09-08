"""Versioned prompt loading.

A prompt is part of the pipeline, so it is versioned like code -- but by content
hash rather than by hand. Edit a prompt and its version changes automatically,
which marks every fact produced by the previous wording as stale and lets the
pipeline reprocess exactly those chunks.

Forgetting to bump a version by hand is the classic way to end up with a graph
whose provenance quietly lies. This removes the opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from string import Template

from diairy.core.hashing import digest_of

_PROMPT_PACKAGE = "diairy.nlp"
_PROMPT_DIR = "prompts"
_VERSION_CHARS = 12


@dataclass(frozen=True)
class Prompt:
    """A system message plus a user-message template, versioned by content."""

    name: str
    version: str
    system: str
    template: str

    def render(self, **values: str) -> str:
        """Fill the template.

        Uses ``string.Template`` rather than ``str.format`` because prompts
        contain JSON examples, and braces should not be format placeholders.
        """
        return Template(self.template).substitute(**values)


def _read(resource: str) -> str:
    location = resources.files(_PROMPT_PACKAGE).joinpath(_PROMPT_DIR, resource)
    return location.read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def load_prompt(name: str) -> Prompt:
    """Load the ``<name>.system.md`` / ``<name>.user.md`` prompt pair."""
    system = _read(f"{name}.system.md")
    template = _read(f"{name}.user.md")
    return Prompt(
        name=name,
        version=digest_of([name, system, template], length=_VERSION_CHARS),
        system=system,
        template=template,
    )
