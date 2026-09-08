"""Making an open ontology queryable.

The model is free to name things however it likes, in whatever language the
entry was written in. Left alone, that produces ``Personne``, ``personne``,
``person`` and ``être humain`` as four unrelated types within a month, and a
graph nobody can query.

Canonicalisation is the answer, and it is deliberately applied *after* storage,
never before. The raw labels are kept verbatim forever; this module builds a
view on top of them. That means a better embedding model, a corrected alias or a
changed threshold can be replayed over the whole history without any loss --
which is exactly what an open ontology needs to stay honest.

Three passes, cheapest first. Only the last one costs a model call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from diairy.core.ids import concept_id
from diairy.core.models import Alias, CanonicalConcept, ConceptRef
from diairy.core.text import normalize_label
from diairy.nlp.provider import EmbeddingProvider

Method = Literal["exact", "normalized", "embedding", "new"]

DEFAULT_THRESHOLD = 0.86


@dataclass(frozen=True)
class Decision:
    """How one raw label was resolved, and by which pass."""

    raw_label: str
    raw_type: str
    canonical_id: str
    canonical_label: str
    canonical_type: str
    method: Method
    score: float

    @property
    def is_new(self) -> bool:
        return self.method == "new"

    def to_alias(self) -> Alias | None:
        """Record the mapping, unless this label *created* the concept."""
        # Compared literally rather than via is_new so the type checker can see
        # that "new" is excluded from the Alias method values below.
        if self.method == "new":
            return None
        return Alias(
            raw_label=self.raw_label,
            raw_type=self.raw_type,
            canonical_id=self.canonical_id,
            method=self.method,
            score=self.score,
        )


def _surface(label: str, type_name: str) -> str:
    """The string actually embedded: type gives the label its context."""
    return f"{type_name}: {label}"


def _cosine_matrix(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity between two batches of row vectors."""
    left_norm = np.linalg.norm(left, axis=1, keepdims=True)
    right_norm = np.linalg.norm(right, axis=1, keepdims=True)
    left_unit = np.divide(left, left_norm, out=np.zeros_like(left), where=left_norm > 0)
    right_unit = np.divide(right, right_norm, out=np.zeros_like(right), where=right_norm > 0)
    return np.asarray(left_unit @ right_unit.T)


class Canonicalizer:
    """Resolves raw concept references onto canonical concepts."""

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._threshold = threshold
        self._embedder = embedder

    def resolve(
        self,
        refs: Sequence[ConceptRef],
        known: Sequence[CanonicalConcept],
    ) -> list[Decision]:
        """Map every reference onto a canonical concept, old or newly minted.

        References that resolve to nothing known become new concepts, and later
        references in the same batch can attach to them -- so a label appearing
        twice in one run produces one concept, not two.
        """
        pool: list[CanonicalConcept] = list(known)
        by_exact = {(item.label, item.type): item for item in pool}
        by_normalized = {
            (normalize_label(item.label), normalize_label(item.type)): item for item in pool
        }

        decisions: list[Decision] = []
        unresolved: list[tuple[int, ConceptRef]] = []

        for index, ref in enumerate(refs):
            exact = by_exact.get((ref.label, ref.type))
            if exact is not None:
                decisions.append(self._decide(ref, exact, "exact", 1.0))
                continue
            normalized = by_normalized.get((normalize_label(ref.label), normalize_label(ref.type)))
            if normalized is not None:
                decisions.append(self._decide(ref, normalized, "normalized", 1.0))
                continue
            decisions.append(self._mint(ref))  # provisional, may be replaced below
            unresolved.append((index, ref))

        if self._embedder is not None and unresolved and pool:
            self._resolve_by_embedding(decisions, unresolved, pool)

        # Concepts minted in this batch become candidates for later references.
        for decision in decisions:
            if decision.is_new:
                minted = CanonicalConcept(
                    id=decision.canonical_id,
                    label=decision.canonical_label,
                    type=decision.canonical_type,
                )
                by_exact.setdefault((minted.label, minted.type), minted)
                by_normalized.setdefault(
                    (normalize_label(minted.label), normalize_label(minted.type)), minted
                )
        return decisions

    def _resolve_by_embedding(
        self,
        decisions: list[Decision],
        unresolved: list[tuple[int, ConceptRef]],
        pool: list[CanonicalConcept],
    ) -> None:
        """Replace provisional decisions that are close enough to a known concept."""
        assert self._embedder is not None  # noqa: S101 -- guarded by the caller
        candidate_texts = [_surface(ref.label, ref.type) for _, ref in unresolved]
        pool_texts = [_surface(item.label, item.type) for item in pool]
        candidate_vectors = np.asarray(self._embedder.embed(candidate_texts), dtype=float)
        pool_vectors = np.asarray(self._embedder.embed(pool_texts), dtype=float)
        if candidate_vectors.size == 0 or pool_vectors.size == 0:
            return

        similarity = _cosine_matrix(candidate_vectors, pool_vectors)
        for row, (index, ref) in enumerate(unresolved):
            best_column = int(np.argmax(similarity[row]))
            score = float(similarity[row][best_column])
            if score >= self._threshold:
                decisions[index] = self._decide(
                    ref, pool[best_column], "embedding", round(score, 4)
                )

    def _decide(
        self,
        ref: ConceptRef,
        target: CanonicalConcept,
        method: Method,
        score: float,
    ) -> Decision:
        return Decision(
            raw_label=ref.label,
            raw_type=ref.type,
            canonical_id=target.id,
            canonical_label=target.label,
            canonical_type=target.type,
            method=method,
            score=score,
        )

    def _mint(self, ref: ConceptRef) -> Decision:
        """Create a new canonical concept from a reference we have not seen."""
        canonical_label = normalize_label(ref.label)
        canonical_type = normalize_label(ref.type)
        return Decision(
            raw_label=ref.label,
            raw_type=ref.type,
            canonical_id=concept_id(canonical_label, canonical_type),
            canonical_label=canonical_label,
            canonical_type=canonical_type,
            method="new",
            score=1.0,
        )
