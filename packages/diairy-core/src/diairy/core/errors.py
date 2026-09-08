"""Exception hierarchy shared by every diAIry package."""

from __future__ import annotations


class DiairyError(Exception):
    """Base class for every error raised by diAIry."""


class ConfigError(DiairyError):
    """The configuration is missing or inconsistent."""


class VaultError(DiairyError):
    """The vault could not be read, or is not in the expected shape."""


class StoreError(DiairyError):
    """The derived store could not be read or written."""


class ProviderError(DiairyError):
    """A model provider (LLM, embeddings, ASR) failed or answered unusably."""


class EgressBlockedError(DiairyError):
    """A connection to a non-allowlisted address was attempted and refused.

    diAIry is offline by construction. Seeing this exception means some code
    path -- ours or a dependency's -- tried to leave the machine. That is a bug
    to fix, never a check to relax.
    """
