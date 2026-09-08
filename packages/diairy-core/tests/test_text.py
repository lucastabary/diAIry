"""Quote location is the hallucination filter, so it gets the most tests."""

from __future__ import annotations

import pytest

from diairy.core.text import (
    collapse_whitespace,
    locate_quote,
    normalize_label,
    strip_accents,
)

SOURCE = (
    "Ce matin j'ai relu Borges.\n"
    "La bibliothèque de Babel me hante encore,\n"
    "surtout la partie sur les catalogues."
)


def test_exact_quote_is_located() -> None:
    span = locate_quote(SOURCE, "j'ai relu Borges")
    assert span is not None
    start, end = span
    assert SOURCE[start:end] == "j'ai relu Borges"


def test_quote_spanning_a_line_break_is_located() -> None:
    span = locate_quote(SOURCE, "La bibliothèque de Babel me hante encore, surtout la partie")
    assert span is not None
    start, end = span
    assert SOURCE[start:end].startswith("La bibliothèque")
    assert SOURCE[start:end].endswith("la partie")


def test_quote_with_different_spacing_is_located() -> None:
    span = locate_quote(SOURCE, "  j'ai   relu    Borges  ")
    assert span is not None
    start, end = span
    assert SOURCE[start:end] == "j'ai relu Borges"


def test_quote_with_different_case_is_located() -> None:
    span = locate_quote(SOURCE, "LA BIBLIOTHÈQUE DE BABEL")
    assert span is not None


def test_invented_quote_is_not_located() -> None:
    assert locate_quote(SOURCE, "j'ai relu Calvino") is None


def test_empty_quote_is_not_located() -> None:
    assert locate_quote(SOURCE, "   ") is None


def test_located_span_always_slices_back_to_the_source() -> None:
    """The property everything downstream depends on."""
    for quote in ("Borges", "catalogues", "me hante encore"):
        span = locate_quote(SOURCE, quote)
        assert span is not None
        start, end = span
        assert 0 <= start < end <= len(SOURCE)
        assert quote.casefold() in SOURCE[start:end].casefold()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Personne", "personne"),
        ("  PERSONNE  ", "personne"),
        ("Cinéma", "cinema"),
        ('"projet"', "projet"),
        ("idée   de   roman", "idee de roman"),
    ],
)
def test_normalize_label(raw: str, expected: str) -> None:
    assert normalize_label(raw) == expected


def test_normalize_label_does_not_merge_genuinely_different_labels() -> None:
    assert normalize_label("Marc") != normalize_label("Marco")


def test_strip_accents_and_collapse_whitespace() -> None:
    assert strip_accents("élève") == "eleve"
    assert collapse_whitespace(" a \n b \t c ") == "a b c"
