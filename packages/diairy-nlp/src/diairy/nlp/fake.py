"""Deterministic stand-ins for the model backends.

These are what let the entire pipeline run in CI, on any machine, with no model
downloaded and no network. They are part of the shipped package rather than the
test folder because every package tests against them.

``CassetteLLM`` is the important one: we record real responses from a local
model once, commit the cassette, and replay it forever. Tests then exercise the
real parsing, validation and storage paths against real model output, without
the non-determinism.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from diairy.core.errors import ProviderError
from diairy.core.hashing import digest_of
from diairy.nlp.provider import LLMResponse


def prompt_digest(system: str, prompt: str) -> str:
    """Stable key for a prompt pair, used to index cassettes."""
    return digest_of(["prompt", system, prompt])


@dataclass
class ScriptedLLM:
    """Returns canned responses, in order or keyed by prompt digest."""

    responses: Sequence[str] | Mapping[str, str] = field(default_factory=list)
    model_name: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list, init=False)
    _cursor: int = field(default=0, init=False)

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return self.model_name

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,  # noqa: ARG002 -- interface parity
        temperature: float = 0.0,  # noqa: ARG002 -- interface parity
    ) -> LLMResponse:
        self.calls.append((system, prompt))
        if isinstance(self.responses, Mapping):
            key = prompt_digest(system, prompt)
            if key not in self.responses:
                message = f"No scripted response for prompt digest {key}"
                raise ProviderError(message)
            text = self.responses[key]
        else:
            if self._cursor >= len(self.responses):
                message = (
                    f"ScriptedLLM ran out of responses after {self._cursor} calls. "
                    f"The code under test made more model calls than expected."
                )
                raise ProviderError(message)
            text = self.responses[self._cursor]
            self._cursor += 1
        return LLMResponse(text=text, model=self.model_name)


class CassetteLLM:
    """Replays model responses recorded from a real local run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            message = f"Cassette {path} could not be read: {exc}"
            raise ProviderError(message) from exc
        except json.JSONDecodeError as exc:
            message = f"Cassette {path} is not valid JSON: {exc}"
            raise ProviderError(message) from exc
        self._model: str = raw.get("model", "cassette")
        self._entries: dict[str, str] = raw.get("entries", {})

    @property
    def name(self) -> str:
        return "cassette"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,  # noqa: ARG002 -- interface parity
        temperature: float = 0.0,  # noqa: ARG002 -- interface parity
    ) -> LLMResponse:
        key = prompt_digest(system, prompt)
        if key not in self._entries:
            message = (
                f"Cassette {self.path.name} has no recording for prompt {key}. "
                f"Re-record it with `diairy record-cassette` on a machine with a model."
            )
            raise ProviderError(message)
        return LLMResponse(text=self._entries[key], model=self._model)


class HashingEmbeddings:
    """Deterministic lexical embeddings, with no model behind them.

    Character trigrams are hashed into a fixed number of buckets and the result
    is L2-normalised. This is not semantic -- it cannot tell that ``bike`` and
    ``bicycle`` are related -- but it *is* stable across processes and machines
    and it does capture surface similarity, which is exactly what the
    canonicalisation tests need to assert against.

    The bucket index comes from SHA-256 rather than :func:`hash`, because
    Python's string hashing is randomised per process.
    """

    def __init__(self, dimensions: int = 64, model_name: str = "hashing-trigram") -> None:
        self._dimensions = dimensions
        self._model = model_name

    @property
    def name(self) -> str:
        return "hashing"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            padded = f"  {text.casefold()}  "
            counts = [0.0] * self._dimensions
            for index in range(len(padded) - 2):
                counts[self._bucket(padded[index : index + 3])] += 1.0
            norm = math.sqrt(sum(value * value for value in counts))
            vectors.append([value / norm for value in counts] if norm else counts)
        return vectors
