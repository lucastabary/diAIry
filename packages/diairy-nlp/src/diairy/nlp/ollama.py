"""Ollama adapter.

Ollama is the default backend because it is the only one that installs in one
step on both macOS and Windows and exposes the same HTTP API on each. It runs on
loopback, which is the single address the egress guard lets through.

This adapter is deliberately thin. Everything interesting lives above it, in
code that has no idea which backend is answering.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from diairy.core.errors import ProviderError
from diairy.nlp.provider import LLMResponse

_MILLISECONDS = 1000


def _unreachable(base_url: str, exc: Exception) -> ProviderError:
    return ProviderError(
        f"Could not reach the local model server at {base_url}: {exc}. "
        f"Start it with `ollama serve` and check the port in your config."
    )


class OllamaLLM:
    """Text generation through a local Ollama server."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 600.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_schema is not None:
            payload["format"] = json_schema

        started = time.monotonic()
        try:
            response = self._client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            message = f"The model server rejected the request: {exc.response.text[:500]}"
            raise ProviderError(message) from exc
        except httpx.HTTPError as exc:
            raise _unreachable(self._base_url, exc) from exc
        except ValueError as exc:
            message = "The model server returned a body that is not JSON."
            raise ProviderError(message) from exc

        elapsed_ms = int((time.monotonic() - started) * _MILLISECONDS)
        message_content = body.get("message", {}).get("content", "")
        return LLMResponse(
            text=message_content,
            model=str(body.get("model", self._model)),
            duration_ms=elapsed_ms,
            prompt_tokens=int(body.get("prompt_eval_count", 0)),
            completion_tokens=int(body.get("eval_count", 0)),
            raw=body,
        )

    def close(self) -> None:
        self._client.close()


class OllamaEmbeddings:
    """Embeddings through a local Ollama server."""

    def __init__(
        self,
        model: str,
        *,
        dimensions: int,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 600.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": list(texts)},
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            message = f"The embedding model rejected the request: {exc.response.text[:500]}"
            raise ProviderError(message) from exc
        except httpx.HTTPError as exc:
            raise _unreachable(self._base_url, exc) from exc
        except ValueError as exc:
            message = "The embedding server returned a body that is not JSON."
            raise ProviderError(message) from exc

        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            message = (
                f"Expected {len(texts)} embeddings, got "
                f"{len(vectors) if isinstance(vectors, list) else 'none'}."
            )
            raise ProviderError(message)
        # A silent dimension change would corrupt the whole index, so it is a
        # hard error rather than something to paper over.
        if vectors and len(vectors[0]) != self._dimensions:
            message = (
                f"Model {self._model} returned {len(vectors[0])}-dimensional vectors "
                f"but the index expects {self._dimensions}. Rebuild the index or fix "
                f"the model registry."
            )
            raise ProviderError(message)
        return [[float(value) for value in vector] for vector in vectors]

    def close(self) -> None:
        self._client.close()
