"""Building providers from configuration.

The one place in the codebase that maps a backend name to a concrete class.
Everything else receives a provider and asks no questions.
"""

from __future__ import annotations

from diairy.core.config import Settings
from diairy.core.errors import ConfigError
from diairy.nlp.fake import HashingEmbeddings, ScriptedLLM, ScriptedTranscription
from diairy.nlp.ollama import OllamaEmbeddings, OllamaLLM
from diairy.nlp.provider import EmbeddingProvider, LLMProvider, TranscriptionProvider
from diairy.nlp.registry import ModelSpec, Profile, load_registry

DEFAULT_FAKE_DIMENSIONS = 64


def resolve_profile(settings: Settings) -> Profile:
    """Return the profile this machine is configured to use."""
    return load_registry().profile(settings.model_profile)


def build_llm(spec: ModelSpec, settings: Settings) -> LLMProvider:
    """Instantiate the text-generation backend named by ``spec``."""
    if spec.backend == "ollama":
        return OllamaLLM(
            spec.model,
            base_url=settings.ollama_base_url,
            timeout=settings.request_timeout_seconds,
        )
    if spec.backend == "fake":
        return ScriptedLLM(model_name=spec.model)
    message = (
        f"Unknown LLM backend {spec.backend!r}. Add an adapter and register it "
        f"in diairy.nlp.factory."
    )
    raise ConfigError(message)


def build_embedder(spec: ModelSpec, settings: Settings) -> EmbeddingProvider:
    """Instantiate the embedding backend named by ``spec``."""
    if spec.backend == "ollama":
        if spec.dimensions is None:
            message = (
                f"Embedding model {spec.model!r} declares no dimensions in the "
                f"registry. The vector index cannot be sized without it."
            )
            raise ConfigError(message)
        return OllamaEmbeddings(
            spec.model,
            dimensions=spec.dimensions,
            base_url=settings.ollama_base_url,
            timeout=settings.request_timeout_seconds,
        )
    if spec.backend == "fake":
        return HashingEmbeddings(
            dimensions=spec.dimensions or DEFAULT_FAKE_DIMENSIONS,
            model_name=spec.model,
        )
    message = (
        f"Unknown embedding backend {spec.backend!r}. Add an adapter and register "
        f"it in diairy.nlp.factory."
    )
    raise ConfigError(message)


def build_transcriber(spec: ModelSpec, settings: Settings) -> TranscriptionProvider:
    """Instantiate the speech-to-text backend named by ``spec``.

    Constructing the provider is cheap: ``faster-whisper`` is imported and the
    weights are loaded lazily, on the first call to ``transcribe``.
    """
    if spec.backend == "faster-whisper":
        # Imported here so the optional dependency is only touched on the path
        # that actually needs it. See diairy.nlp.whisper for why it stays lazy.
        from diairy.nlp.whisper import FasterWhisperTranscription  # noqa: PLC0415

        return FasterWhisperTranscription(
            spec.model,
            download_root=str(settings.models_dir),
            device=settings.transcription_device,
            compute_type=settings.transcription_compute_type,
        )
    if spec.backend == "fake":
        return ScriptedTranscription()
    message = (
        f"Unknown transcription backend {spec.backend!r}. Add an adapter and "
        f"register it in diairy.nlp.factory."
    )
    raise ConfigError(message)
