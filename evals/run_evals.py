"""Measure extraction quality against the French fixture corpus.

Never runs in CI: it needs model weights and it is non-deterministic. Run it on
a machine that has a model, after changing a prompt, a model or the extraction
schema, and record the number in registry.toml.

    uv run python evals/run_evals.py --profile medium
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from diairy.core.clock import utc_now
from diairy.core.config import Settings
from diairy.core.egress import install_egress_guard
from diairy.core.errors import DiairyError
from diairy.core.ids import chunk_id, document_version_id
from diairy.core.models import Chunk, Fact
from diairy.core.text import normalize_label
from diairy.nlp.extract import FactExtractor, Vocabulary
from diairy.nlp.factory import build_llm, resolve_profile
from diairy.vault.chunking import chunk_blocks
from diairy.vault.markdown import parse_document, split_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "fr"


@dataclass
class FixtureResult:
    """What one fixture produced."""

    name: str
    expected: int = 0
    found: int = 0
    missed: list[str] = field(default_factory=list)
    extracted: int = 0
    rejected: int = 0
    seconds: float = 0.0

    @property
    def recall(self) -> float:
        return self.found / self.expected if self.expected else 1.0

    @property
    def rejection_rate(self) -> float:
        total = self.extracted + self.rejected
        return self.rejected / total if total else 0.0


def matches(expected: dict[str, str], fact: Fact) -> bool:
    """Loose match: did the model understand, regardless of its wording.

    Exact matching would be meaningless against an open ontology, where the
    model legitimately chooses its own labels.
    """
    parts = (
        (expected.get("subject", ""), fact.subject.label),
        (expected.get("predicate", ""), fact.predicate),
        (expected.get("object", ""), fact.object.label),
    )
    return all(
        not normalize_label(wanted) or normalize_label(wanted) in normalize_label(got)
        for wanted, got in parts
    )


def chunks_for(text: str, settings: Settings, version: str) -> list[Chunk]:
    """Segment a fixture exactly as the pipeline would."""
    parsed = parse_document(text)
    blocks = split_blocks(parsed.body, offset=parsed.body_offset)
    spans = chunk_blocks(
        text,
        blocks,
        target_chars=settings.chunk_target_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    return [
        Chunk(
            id=chunk_id(version, span.char_start, span.char_end),
            document_version_id=version,
            ordinal=ordinal,
            char_start=span.char_start,
            char_end=span.char_end,
            text=span.text,
            heading_path=span.heading_path,
        )
        for ordinal, span in enumerate(spans)
    ]


def run_fixture(path: Path, extractor: FactExtractor, settings: Settings) -> FixtureResult:
    """Extract from one fixture and score it against its expectations."""
    expectations: list[dict[str, str]] = json.loads(
        path.with_suffix(".expected.json").read_text(encoding="utf-8")
    )
    result = FixtureResult(name=path.stem, expected=len(expectations))

    text = path.read_text(encoding="utf-8")
    version = document_version_id(path.stem, path.stem)
    started = time.monotonic()

    facts: list[Fact] = []
    for chunk in chunks_for(text, settings, version):
        outcome = extractor.extract(
            chunk, run_id="eval", event_time=utc_now(), vocabulary=Vocabulary()
        )
        facts.extend(outcome.facts)
        result.rejected += len(outcome.rejected)

    result.seconds = time.monotonic() - started
    result.extracted = len(facts)
    for expected in expectations:
        if any(matches(expected, fact) for fact in facts):
            result.found += 1
        else:
            result.missed.append(
                f"{expected.get('subject', '?')} / {expected.get('predicate', '?')} "
                f"/ {expected.get('object', '?')}"
            )
    return result


def report(results: list[FixtureResult], profile: str, model: str) -> dict[str, Any]:
    """Print a human-readable report and return it as data."""
    expected = sum(item.expected for item in results)
    found = sum(item.found for item in results)
    extracted = sum(item.extracted for item in results)
    rejected = sum(item.rejected for item in results)
    seconds = sum(item.seconds for item in results)

    print(f"\nprofile {profile}  model {model}  fixtures {len(results)}")
    print("-" * 72)
    for item in results:
        print(
            f"{item.name:<28} recall {item.recall:5.0%}  "
            f"kept {item.extracted:>3}  rejected {item.rejected:>3}  "
            f"{item.seconds:5.1f}s"
        )
        for miss in item.missed:
            print(f"    missed: {miss}")
    print("-" * 72)
    overall_recall = found / expected if expected else 1.0
    overall_rejection = rejected / (extracted + rejected) if extracted + rejected else 0.0
    print(
        f"recall {overall_recall:.0%} ({found}/{expected})   "
        f"rejection {overall_rejection:.0%}   "
        f"yield {extracted}   total {seconds:.1f}s"
    )
    print("\nRecord this in registry.toml above the profile you measured.\n")

    return {
        "profile": profile,
        "model": model,
        "measured_at": utc_now().isoformat(),
        "recall": round(overall_recall, 4),
        "rejection_rate": round(overall_rejection, 4),
        "facts_kept": extracted,
        "seconds": round(seconds, 2),
        "fixtures": [
            {
                "name": item.name,
                "recall": round(item.recall, 4),
                "missed": item.missed,
            }
            for item in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="Model profile to evaluate.")
    parser.add_argument("--json", type=Path, help="Also write the report here.")
    args = parser.parse_args(argv)

    settings = Settings(model_profile=args.profile) if args.profile else Settings()
    # Evals talk to the local model server, and nothing else.
    install_egress_guard(allow_loopback=True, allowlist=[settings.ollama_address])

    try:
        profile = resolve_profile(settings)
    except DiairyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if profile.extraction.backend == "fake":
        print(
            f"Profile {settings.model_profile!r} uses a deterministic stub, so there "
            f"is nothing to evaluate.",
            file=sys.stderr,
        )
        print(
            "Evals need real weights: run with --profile small, medium or large, "
            "and make sure Ollama is running.",
            file=sys.stderr,
        )
        return 2

    extractor = FactExtractor(build_llm(profile.extraction, settings))

    fixtures = sorted(FIXTURES.glob("*.md"))
    if not fixtures:
        print(f"No fixtures found in {FIXTURES}", file=sys.stderr)
        return 1

    try:
        results = [run_fixture(path, extractor, settings) for path in fixtures]
    except DiairyError as exc:
        print(f"Eval run failed: {exc}", file=sys.stderr)
        return 1
    payload = report(results, settings.model_profile, profile.extraction.model)

    if args.json:
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
