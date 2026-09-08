"""Configuration.

Precedence, highest first: explicit arguments, environment (``DIAIRY_*``),
the TOML config file, then defaults.

The vault path is deliberately free: it can point at a USB stick, an external
drive, an encrypted volume -- anywhere the user wants their raw journal to
live. Everything under ``data_dir`` is derived and can be deleted and rebuilt.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from diairy.core.errors import ConfigError

APP_NAME = "diAIry"
ENV_PREFIX = "DIAIRY_"


def default_config_path() -> Path:
    """Return the OS-appropriate location of ``config.toml``."""
    override = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if override:
        return Path(override)
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.toml"


def _default_data_dir() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False))


def _default_vault_path() -> Path:
    return Path.home() / APP_NAME / "vault"


class Settings(BaseSettings):
    """Everything the application needs to know about this machine."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid")

    vault_path: Path = Field(default_factory=_default_vault_path)
    """Where the raw Markdown lives. The only irreplaceable directory."""

    data_dir: Path = Field(default_factory=_default_data_dir)
    """Where derived stores live. Fully rebuildable from the vault."""

    model_profile: str = "medium"
    """Which entry of ``models/registry.toml`` to use on this machine."""

    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434
    request_timeout_seconds: float = 600.0
    """Generous on purpose: this is a nightly batch, not an interactive UI."""

    transcription_device: str = "auto"
    """Where faster-whisper runs: ``auto`` picks CUDA if present, else CPU."""

    transcription_compute_type: str = "int8"
    """faster-whisper quantisation. ``int8`` is the light default for CPU."""

    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 120
    canonicalization_threshold: float = 0.86
    """Cosine similarity above which two labels are treated as one concept."""

    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        """The single SQLite file holding facts, full-text and vector indexes."""
        return self.data_dir / "diairy.db"

    @property
    def graph_path(self) -> Path:
        """The Kuzu database directory. A projection; safe to delete."""
        return self.data_dir / "graph"

    @property
    def runs_dir(self) -> Path:
        """Where raw model prompts and responses are archived, run by run."""
        return self.data_dir / "runs"

    @property
    def models_dir(self) -> Path:
        """Where downloaded model weights live (e.g. faster-whisper's).

        Derived and re-fetchable, so it sits under ``data_dir`` with the rest of
        the rebuildable state, never in the vault.
        """
        return self.data_dir / "models"

    @property
    def ollama_base_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def ollama_address(self) -> tuple[str, int]:
        """The one address the egress guard is allowed to let through."""
        return self.ollama_host, self.ollama_port

    def ensure_directories(self) -> None:
        """Create the derived directories. Never touches the vault."""
        for directory in (self.data_dir, self.runs_dir, self.models_dir):
            directory.mkdir(parents=True, exist_ok=True)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Could not read the config file at {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    return data


def load_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    """Build :class:`Settings` from file, environment and explicit overrides."""
    path = config_path or default_config_path()
    values = _read_toml(path) if path.is_file() else {}
    # The environment outranks the file, so drop any key the environment sets.
    for key in list(values):
        if f"{ENV_PREFIX}{key.upper()}" in os.environ:
            del values[key]
    values.update(overrides)
    return Settings(**values)
