"""Deterministic enrichment: everything we can know without a model.

This stage runs before the LLM, and it earns its place twice over. What it finds
is exact, free, instant and genuinely unit-testable -- unlike anything a model
produces. And what it finds is fed into the extraction prompt as context, which
measurably reduces the model's need to guess.

The rule the project follows: never ask a model for something a regular
expression already knows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL = re.compile(r"https?://[^\s<>\"'\])]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HASHTAG = re.compile(r"(?<![\w#])#([^\s#,.;:!?]{2,50})")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_MENTION = re.compile(r"(?<![\w@])@([\w.-]{2,50})")
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_CLOCK_TIME = re.compile(r"\b([01]?\d|2[0-3])[h:]([0-5]\d)\b")
_FS_PATH = re.compile(r"(?:[A-Za-z]:\\|~/|\./|/)[\w./\\-]{2,200}")

# Frequent, short, and rarely shared between the two languages. Enough to tell
# French from English on a two-sentence note, which is all we need here.
_STOPWORDS: dict[str, frozenset[str]] = {
    "fr": frozenset(
        """je tu il elle nous vous ils elles le la les un une des du de et est sont
        pas plus pour dans avec sur que qui quoi mais donc car ce cette ces mon ma
        mes ton ta tes son sa ses au aux fait faire ete etre avoir tres bien alors
        aujourd hui hier demain parce comme quand""".split()  # noqa: SIM905 -- see above
    ),
    "en": frozenset(
        """i you he she we they the a an of and is are was were not for in with on
        that which what but so because this these those my your his her its at to
        from have has had been being very well then today yesterday tomorrow
        when like just about""".split()  # noqa: SIM905 -- a block reads better than 60 literals
    ),
}

UNKNOWN_LANGUAGE = "und"
_MIN_LANGUAGE_TOKENS = 5
_MIN_LANGUAGE_MARGIN = 1


@dataclass(frozen=True)
class Signals:
    """Facts about a chunk that required no model at all."""

    language: str
    urls: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    wikilinks: tuple[str, ...] = ()
    mentions: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    times: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not any(
            (
                self.urls,
                self.emails,
                self.hashtags,
                self.wikilinks,
                self.mentions,
                self.dates,
                self.times,
                self.paths,
            )
        )


def _unique(matches: list[str]) -> tuple[str, ...]:
    """De-duplicate while preserving first-seen order."""
    return tuple(dict.fromkeys(match.strip() for match in matches if match.strip()))


def detect_language(text: str) -> str:
    """Guess the language from stopword frequency.

    Intentionally tiny: it feeds a prompt hint and picks an embedding path, and
    a wrong guess degrades quality rather than breaking anything. A real
    detector is on the roadmap, behind this same function signature.
    """
    tokens = re.findall(r"[^\W\d_]+", text.casefold(), flags=re.UNICODE)
    if len(tokens) < _MIN_LANGUAGE_TOKENS:
        return UNKNOWN_LANGUAGE
    scores = {
        language: sum(token in words for token in tokens) for language, words in _STOPWORDS.items()
    }
    best, best_score = max(scores.items(), key=lambda item: item[1])
    runner_up = max((score for lang, score in scores.items() if lang != best), default=0)
    if best_score == 0 or best_score - runner_up < _MIN_LANGUAGE_MARGIN:
        return UNKNOWN_LANGUAGE
    return best


def extract_signals(text: str) -> Signals:
    """Collect every deterministic signal from a chunk of text."""
    return Signals(
        language=detect_language(text),
        urls=_unique(_URL.findall(text)),
        emails=_unique(_EMAIL.findall(text)),
        hashtags=_unique(_HASHTAG.findall(text)),
        wikilinks=_unique(_WIKILINK.findall(text)),
        mentions=_unique(_MENTION.findall(text)),
        dates=_unique(_ISO_DATE.findall(text)),
        times=_unique([f"{hour}:{minute}" for hour, minute in _CLOCK_TIME.findall(text)]),
        paths=_unique(_FS_PATH.findall(text)),
    )


def format_signals(signals: Signals) -> str:
    """Render signals as the prompt fragment handed to the extraction model."""
    if signals.is_empty():
        return f"language: {signals.language}\n(no other signals detected)"
    lines = [f"language: {signals.language}"]
    for label, values in (
        ("urls", signals.urls),
        ("emails", signals.emails),
        ("hashtags", signals.hashtags),
        ("wikilinks", signals.wikilinks),
        ("mentions", signals.mentions),
        ("dates", signals.dates),
        ("times", signals.times),
        ("paths", signals.paths),
    ):
        if values:
            lines.append(f"{label}: {', '.join(values)}")
    return "\n".join(lines)
