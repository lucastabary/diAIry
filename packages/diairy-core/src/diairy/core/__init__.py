"""Shared domain model, identifiers and invariants for diAIry."""

from diairy.core.clock import ensure_aware, from_iso, to_iso, utc_now
from diairy.core.config import Settings, default_config_path, load_settings
from diairy.core.egress import egress_guard, install_egress_guard, uninstall_egress_guard
from diairy.core.errors import (
    ConfigError,
    DiairyError,
    EgressBlockedError,
    ProviderError,
    StoreError,
    VaultError,
)
from diairy.core.models import (
    LITERAL_TYPE,
    Alias,
    CanonicalConcept,
    Chunk,
    ConceptRef,
    Document,
    DocumentVersion,
    Evidence,
    Fact,
    Run,
    RunStatus,
)

__all__ = [
    "LITERAL_TYPE",
    "Alias",
    "CanonicalConcept",
    "Chunk",
    "ConceptRef",
    "ConfigError",
    "DiairyError",
    "Document",
    "DocumentVersion",
    "EgressBlockedError",
    "Evidence",
    "Fact",
    "ProviderError",
    "Run",
    "RunStatus",
    "Settings",
    "StoreError",
    "VaultError",
    "default_config_path",
    "egress_guard",
    "ensure_aware",
    "from_iso",
    "install_egress_guard",
    "load_settings",
    "to_iso",
    "uninstall_egress_guard",
    "utc_now",
]
