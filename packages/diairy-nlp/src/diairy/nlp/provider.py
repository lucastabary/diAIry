"""Model provider interfaces.

Everything above this line in the stack talks to these protocols and never to a
concrete backend. That is what lets the Mac run MLX one day while Windows keeps
using Ollama, without a single change in the pipeline, the store or the queries.

Adding a backend means writing one adapter that satisfies these protocols and
passes the shared conformance tests. Nothing else moves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResponse:
    """One completion, plus everything needed to reproduce and audit it."""

    text: str
    model: str
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """A local text-generation backend."""

    @property
    def name(self) -> str:
        """Backend identifier, recorded on every run (e.g. ``ollama``)."""
        ...

    @property
    def model(self) -> str:
        """The concrete model this provider was configured with."""
        ...

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Generate a completion.

        When ``json_schema`` is given the backend must constrain decoding to it.
        Constrained decoding is not a nicety here: it is what turns an open
        ontology into something a parser can still rely on.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A local embedding backend."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int:
        """Vector width. Must stay stable for the life of an index."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, preserving order."""
        ...


@runtime_checkable
class TranscriptionProvider(Protocol):
    """A local speech-to-text backend. Voice notes become Markdown, then flow
    through exactly the same pipeline as anything typed."""

    @property
    def name(self) -> str: ...

    def transcribe(self, audio_path: str, *, language: str | None = None) -> str: ...
