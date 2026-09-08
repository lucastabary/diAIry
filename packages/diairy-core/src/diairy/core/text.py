"""Deterministic text utilities.

Two jobs live here, and both are load-bearing:

``locate_quote``
    The extraction model returns a verbatim quote for every claim it makes. We
    then locate that quote in the source text ourselves. A quote that cannot be
    located is a fabrication, and the fact is dropped. This is the cheapest
    hallucination filter we have, and it costs no model time.

``normalize_label``
    The ontology is open, so ``Personne``, ``personne`` and ``PERSONNE`` will
    all show up. Normalising is the first, free step of canonicalisation --
    before any embedding is involved.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = re.compile(r"^[\s\"'`(\[{.,;:!?-]+|[\s\"'`)\]}.,;:!?-]+$")


def strip_accents(value: str) -> str:
    """Return ``value`` with combining marks removed (``café`` -> ``cafe``)."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def collapse_whitespace(value: str) -> str:
    """Collapse every run of whitespace into a single space and strip the ends."""
    return _WHITESPACE.sub(" ", value).strip()


def normalize_label(value: str) -> str:
    """Normalise a free-form label for exact-match canonicalisation.

    Lowercased, accent-free, whitespace-collapsed and stripped of edge
    punctuation. Deliberately conservative: it must never merge two labels that
    a human would consider different.
    """
    cleaned = _EDGE_PUNCTUATION.sub("", value)
    return collapse_whitespace(strip_accents(cleaned).casefold())


def _normalized_index(text: str) -> tuple[str, list[int]]:
    """Return whitespace-collapsed ``text`` and a map back to original offsets.

    ``offsets[i]`` is the index in ``text`` of the i-th character of the
    normalised string, which is what lets us report spans against the original.
    """
    normalized: list[str] = []
    offsets: list[int] = []
    in_whitespace = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_whitespace and normalized:
                normalized.append(" ")
                offsets.append(index)
            in_whitespace = True
            continue
        in_whitespace = False
        normalized.append(char)
        offsets.append(index)
    while normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def locate_quote(haystack: str, quote: str) -> tuple[int, int] | None:
    """Locate ``quote`` inside ``haystack`` and return its ``(start, end)`` span.

    Three passes, from strictest to most forgiving: exact substring, then
    whitespace-insensitive, then additionally case-insensitive. Returns ``None``
    when the quote is nowhere to be found, which callers must treat as the model
    having invented it.
    """
    stripped = quote.strip()
    if not stripped:
        return None

    exact = haystack.find(stripped)
    if exact != -1:
        return exact, exact + len(stripped)

    normalized_haystack, offsets = _normalized_index(haystack)
    normalized_quote = collapse_whitespace(stripped)
    if not normalized_quote:
        return None

    position = normalized_haystack.find(normalized_quote)
    if position == -1:
        position = normalized_haystack.casefold().find(normalized_quote.casefold())
    if position == -1:
        return None

    start = offsets[position]
    end = offsets[position + len(normalized_quote) - 1] + 1
    return start, end
