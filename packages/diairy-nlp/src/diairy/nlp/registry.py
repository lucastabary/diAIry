"""The model pool.

Every model diAIry can use is declared in ``data/registry.toml`` and nowhere
else. Contributors pick the profile their hardware can run; the pipeline, the
store and the queries never learn which one it was.

That indirection is what makes "he develops on an M1, I develop on a laptop"
workable without forking the codebase, and what will make adding an MLX backend
a registry entry rather than a refactor.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from diairy.core.errors import ConfigError

_REGISTRY_PACKAGE = "diairy.nlp"
_REGISTRY_RESOURCE = "data/registry.toml"


class ModelSpec(BaseModel):
    """One model, as the registry declares it."""

    model_config = ConfigDict(frozen=True, extra="allow")

    backend: str
    model: str
    context_tokens: int = 8192
    dimensions: int | None = None


class Profile(BaseModel):
    """A coherent set of models for one class of machine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""
    min_ram_gb: int = 0
    extraction: ModelSpec
    embedding: ModelSpec
    transcription: ModelSpec | None = None


class Registry(BaseModel):
    """The whole registry file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    default_profile: str = "medium"
    profiles: dict[str, Profile] = Field(default_factory=dict)

    def profile(self, name: str) -> Profile:
        """Look up a profile, failing with the list of what is available."""
        try:
            return self.profiles[name]
        except KeyError:
            available = ", ".join(sorted(self.profiles)) or "(none)"
            message = (
                f"Unknown model profile {name!r}. Available profiles: {available}. "
                f"Set DIAIRY_MODEL_PROFILE or model_profile in your config."
            )
            raise ConfigError(message) from None


def _parse(data: dict[str, Any], origin: str) -> Registry:
    try:
        return Registry.model_validate(data)
    except ValueError as exc:
        message = f"{origin} is not a valid model registry: {exc}"
        raise ConfigError(message) from exc


@lru_cache(maxsize=4)
def load_registry(path: Path | None = None) -> Registry:
    """Load the registry, from ``path`` if given or from the packaged default."""
    if path is not None:
        try:
            with path.open("rb") as handle:
                return _parse(tomllib.load(handle), str(path))
        except OSError as exc:
            message = f"Could not read the model registry at {path}: {exc}"
            raise ConfigError(message) from exc
        except tomllib.TOMLDecodeError as exc:
            message = f"{path} is not valid TOML: {exc}"
            raise ConfigError(message) from exc

    location = resources.files(_REGISTRY_PACKAGE).joinpath(_REGISTRY_RESOURCE)
    return _parse(tomllib.loads(location.read_text(encoding="utf-8")), _REGISTRY_RESOURCE)
