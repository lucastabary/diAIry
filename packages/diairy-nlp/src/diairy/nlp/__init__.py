"""Model providers, constrained extraction, enrichment and canonicalisation."""

from diairy.nlp.canonical import Canonicalizer, Decision
from diairy.nlp.enrich import Signals, detect_language, extract_signals, format_signals
from diairy.nlp.extract import (
    ExtractionOutcome,
    FactExtractor,
    RejectedFact,
    Vocabulary,
)
from diairy.nlp.factory import build_embedder, build_llm, resolve_profile
from diairy.nlp.fake import CassetteLLM, HashingEmbeddings, ScriptedLLM, prompt_digest
from diairy.nlp.prompts import Prompt, load_prompt
from diairy.nlp.provider import (
    EmbeddingProvider,
    LLMProvider,
    LLMResponse,
    TranscriptionProvider,
)
from diairy.nlp.registry import ModelSpec, Profile, Registry, load_registry
from diairy.nlp.schema import ExtractionResult, RawConcept, RawFact, extraction_json_schema

__all__ = [
    "Canonicalizer",
    "CassetteLLM",
    "Decision",
    "EmbeddingProvider",
    "ExtractionOutcome",
    "ExtractionResult",
    "FactExtractor",
    "HashingEmbeddings",
    "LLMProvider",
    "LLMResponse",
    "ModelSpec",
    "Profile",
    "Prompt",
    "RawConcept",
    "RawFact",
    "Registry",
    "RejectedFact",
    "ScriptedLLM",
    "Signals",
    "TranscriptionProvider",
    "Vocabulary",
    "build_embedder",
    "build_llm",
    "detect_language",
    "extract_signals",
    "extraction_json_schema",
    "format_signals",
    "load_prompt",
    "load_registry",
    "prompt_digest",
    "resolve_profile",
]
