"""Canonicalisation and the model-free enrichment stage."""

from __future__ import annotations

from diairy.core.models import CanonicalConcept, ConceptRef
from diairy.nlp.canonical import Canonicalizer
from diairy.nlp.enrich import UNKNOWN_LANGUAGE, detect_language, extract_signals, format_signals
from diairy.nlp.fake import HashingEmbeddings

KNOWN = [
    CanonicalConcept(id="c-personne", label="personne", type="type"),
    CanonicalConcept(id="c-borges", label="borges", type="auteur"),
]


def test_exact_match_costs_nothing() -> None:
    decisions = Canonicalizer().resolve([ConceptRef(label="borges", type="auteur")], KNOWN)
    assert decisions[0].method == "exact"
    assert decisions[0].canonical_id == "c-borges"


def test_case_and_accents_are_absorbed_without_a_model() -> None:
    decisions = Canonicalizer().resolve([ConceptRef(label="BORGES", type="Auteur")], KNOWN)
    assert decisions[0].method == "normalized"
    assert decisions[0].canonical_id == "c-borges"


def test_an_unknown_concept_is_minted() -> None:
    decisions = Canonicalizer().resolve([ConceptRef(label="Eco", type="auteur")], KNOWN)
    assert decisions[0].method == "new"
    assert decisions[0].canonical_label == "eco"
    assert decisions[0].to_alias() is None  # it created the concept, it is not an alias


def test_the_same_new_label_twice_in_one_batch_yields_one_concept() -> None:
    refs = [ConceptRef(label="Eco", type="auteur"), ConceptRef(label="eco", type="auteur")]
    decisions = Canonicalizer().resolve(refs, KNOWN)
    assert decisions[0].canonical_id == decisions[1].canonical_id


def test_embeddings_merge_near_identical_labels() -> None:
    canonicalizer = Canonicalizer(threshold=0.75, embedder=HashingEmbeddings())
    decisions = canonicalizer.resolve([ConceptRef(label="borgès", type="auteur")], KNOWN)
    assert decisions[0].canonical_id == "c-borges"
    assert decisions[0].method in {"normalized", "embedding"}


def test_a_high_threshold_refuses_to_merge_distinct_things() -> None:
    canonicalizer = Canonicalizer(threshold=0.99, embedder=HashingEmbeddings())
    decisions = canonicalizer.resolve([ConceptRef(label="Calvino", type="auteur")], KNOWN)
    assert decisions[0].method == "new"


def test_a_resolved_reference_produces_an_alias_record() -> None:
    decisions = Canonicalizer().resolve([ConceptRef(label="BORGES", type="auteur")], KNOWN)
    alias = decisions[0].to_alias()
    assert alias is not None
    assert alias.raw_label == "BORGES"
    assert alias.canonical_id == "c-borges"


def test_resolving_against_an_empty_graph_mints_everything() -> None:
    decisions = Canonicalizer(embedder=HashingEmbeddings()).resolve(
        [ConceptRef(label="Eco", type="auteur")], []
    )
    assert decisions[0].method == "new"


def test_hashing_embeddings_are_stable_and_normalised() -> None:
    embedder = HashingEmbeddings(dimensions=32)
    first = embedder.embed(["bonjour"])
    second = embedder.embed(["bonjour"])
    assert first == second
    assert len(first[0]) == 32
    assert abs(sum(value * value for value in first[0]) - 1.0) < 1e-9


def test_language_detection_on_short_notes() -> None:
    assert detect_language("Je suis alle au cinema avec elle hier soir") == "fr"
    assert detect_language("I went to the cinema with her yesterday evening") == "en"
    assert detect_language("Borges") == UNKNOWN_LANGUAGE


def test_signals_are_extracted_without_any_model() -> None:
    text = (
        "Note du 2026-03-14 a 21h30 : lire https://example.org/article #lecture "
        "avec @marc, voir [[Le Nom de la rose]] et ~/notes/eco.md"
    )
    signals = extract_signals(text)
    assert signals.urls == ("https://example.org/article",)
    assert signals.hashtags == ("lecture",)
    assert signals.mentions == ("marc",)
    assert signals.wikilinks == ("Le Nom de la rose",)
    assert signals.dates == ("2026-03-14",)
    assert signals.times == ("21:30",)
    assert any("eco.md" in path for path in signals.paths)
    assert not signals.is_empty()


def test_signals_are_deduplicated_in_order() -> None:
    signals = extract_signals("#lecture #roman #lecture")
    assert signals.hashtags == ("lecture", "roman")


def test_markdown_headings_are_not_mistaken_for_hashtags() -> None:
    assert extract_signals("# Journee\n\n## Travail").hashtags == ()


def test_formatting_an_empty_signal_set_still_reports_the_language() -> None:
    rendered = format_signals(extract_signals("Je suis alle au cinema avec elle hier"))
    assert "language: fr" in rendered
    assert "no other signals" in rendered
