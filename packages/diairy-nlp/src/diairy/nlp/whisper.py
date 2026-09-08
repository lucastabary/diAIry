"""Local speech-to-text with faster-whisper.

This is the concrete :class:`~diairy.nlp.provider.TranscriptionProvider` behind
the ``faster-whisper`` backend named in the registry. It turns a recorded audio
file into text, entirely on this machine.

Two properties matter more than anything else here, and both are load-bearing:

* **It never reaches the network.** The model is always loaded with
  ``local_files_only=True``. If the weights are not on disk it raises with the
  command to fetch them, rather than silently downloading. Fetching happens once,
  deliberately, outside the egress guard, through :func:`download_model` (wired
  to ``diairy models pull``). See ADR 0009.
* **It is an optional dependency.** ``faster-whisper`` is heavy and
  platform-specific, so it is an extra. The import lives inside the functions
  that need it, and its absence is reported as something to install, not a
  traceback.

Loaded models are cached at module scope: constructing one is slow, and the web
server builds a fresh context per request, so without the cache every voice note
would reload the weights.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from diairy.core.errors import ProviderError

_INSTALL_HINT = (
    "faster-whisper is not installed. It is an optional extra; install it with "
    "`uv sync --all-packages --extra transcription`."
)

# Keyed by everything that changes which weights are loaded and how. The value
# is a faster_whisper.WhisperModel, kept as Any so this module never names an
# unstubbed third-party type in an annotation.
_model_cache: dict[tuple[str, str, str, str, bool], Any] = {}


def _load_whisper_model(
    model: str,
    *,
    download_root: str,
    device: str,
    compute_type: str,
    local_files_only: bool,
) -> Any:
    """Return a cached WhisperModel, importing faster-whisper on first use."""
    key = (model, device, compute_type, download_root, local_files_only)
    cached = _model_cache.get(key)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 -- optional extra, lazy by design
    except ImportError as exc:  # pragma: no cover -- exercised only without the extra
        raise ProviderError(_INSTALL_HINT) from exc
    try:
        instance = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
            local_files_only=local_files_only,
        )
    except ValueError as exc:
        # faster-whisper raises ValueError when local_files_only is set and the
        # weights are not on disk. Turn it into an instruction, not a stack trace.
        message = (
            f"The transcription model {model!r} is not downloaded. Fetch it once "
            f"with `diairy models pull`, then try again."
        )
        raise ProviderError(message) from exc
    _model_cache[key] = instance
    return instance


def download_model(
    model: str, *, download_root: str, device: str = "cpu", compute_type: str = "int8"
) -> None:
    """Download the weights for ``model`` into ``download_root``.

    This is the one place in the codebase that is *meant* to reach the network,
    and it is only ever reached from ``diairy models pull``, which does not arm
    the egress guard. Everything else loads ``local_files_only=True``. Loading on
    CPU/int8 keeps the download step light regardless of the serving device.
    """
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 -- optional extra, lazy by design
    except ImportError as exc:
        raise ProviderError(_INSTALL_HINT) from exc
    # Constructing the model with local_files_only=False downloads any missing
    # weights as a side effect; we do not need the instance itself.
    WhisperModel(
        model,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
        local_files_only=False,
    )


class FasterWhisperTranscription:
    """A local, offline speech-to-text backend built on faster-whisper."""

    def __init__(
        self,
        model: str,
        *,
        download_root: str,
        device: str = "auto",
        compute_type: str = "int8",
    ) -> None:
        self._model = model
        self._download_root = download_root
        self._device = device
        self._compute_type = compute_type

    @property
    def name(self) -> str:
        return "faster-whisper"

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, audio_path: str, *, language: str | None = None) -> str:
        """Transcribe an audio file to text.

        ``language`` is a hint (e.g. ``"fr"``); left as ``None``, whisper detects
        the spoken language itself, which is what keeps every language working
        through one path. ``vad_filter`` drops silence so a pause does not
        produce hallucinated text.
        """
        if not Path(audio_path).is_file():
            raise ProviderError(f"No audio file at {audio_path} to transcribe.")
        whisper = _load_whisper_model(
            self._model,
            download_root=self._download_root,
            device=self._device,
            compute_type=self._compute_type,
            local_files_only=True,
        )
        segments, _info = whisper.transcribe(audio_path, language=language, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()
