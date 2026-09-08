"""Stage orchestration.

Four stages, each idempotent, each resumable, each safe to run again on an
unchanged vault and do nothing:

``ingest``
    Read the vault, commit it to git, record new document versions and chunks.
    No model involved, so it is fast and can run as often as you like.
``process``
    Extract facts from chunks nobody has extracted from yet, then canonicalise
    the concepts they mention. This is the expensive stage, and the one designed
    to run overnight.
``embed``
    Fill in missing chunk embeddings.
``project``
    Rewrite the graph from the current fact log.

Interrupting any of them loses at most the chunk in flight. The work queue is
derived from the data itself -- "chunks with no facts", "chunks with no vector"
-- rather than from a checkpoint file that can go stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from diairy.core.clock import to_iso, utc_now
from diairy.core.config import Settings
from diairy.core.errors import VaultError
from diairy.core.ids import chunk_id as make_chunk_id
from diairy.core.ids import document_version_id, new_run_id
from diairy.core.models import (
    CanonicalConcept,
    Chunk,
    ConceptRef,
    Document,
    DocumentVersion,
    Fact,
    Run,
)
from diairy.nlp.canonical import Canonicalizer
from diairy.nlp.extract import FactExtractor, Vocabulary
from diairy.nlp.provider import EmbeddingProvider
from diairy.pipeline.runlog import RunLog
from diairy.store.graph import GraphProjection, ProjectionStats
from diairy.store.repository import PendingChunk, Repository
from diairy.store.search import SearchIndex
from diairy.vault.chunking import chunk_blocks
from diairy.vault.git import VaultGit
from diairy.vault.markdown import infer_event_time, parse_document, split_blocks
from diairy.vault.scanner import Vault, VaultFile

PIPELINE_VERSION = "1"
"""Bumped when a stage changes in a way that invalidates previously derived data."""

DEFAULT_EMBED_BATCH = 32
KNOWN_CONCEPT_WINDOW = 2000
"""How many concepts to hold in memory as canonicalisation candidates."""


@dataclass
class IngestStats:
    """What one ingest pass found."""

    documents_seen: int = 0
    documents_new: int = 0
    versions_new: int = 0
    chunks_new: int = 0
    facts_superseded: int = 0
    git_commit: str | None = None

    def as_dict(self) -> dict[str, int]:
        return {
            "documents_seen": self.documents_seen,
            "documents_new": self.documents_new,
            "versions_new": self.versions_new,
            "chunks_new": self.chunks_new,
            "facts_superseded": self.facts_superseded,
        }


@dataclass
class ProcessStats:
    """What one extraction pass produced."""

    run_id: str = ""
    chunks_processed: int = 0
    facts_written: int = 0
    facts_rejected: int = 0
    concepts_new: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        return {
            "chunks_processed": self.chunks_processed,
            "facts_written": self.facts_written,
            "facts_rejected": self.facts_rejected,
            "concepts_new": self.concepts_new,
        }


@dataclass
class EmbedStats:
    """What one embedding pass indexed."""

    chunks_embedded: int = 0
    available: bool = True
    note: str = ""


class Pipeline:
    """Runs the stages against one vault and one store."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: Repository,
        search: SearchIndex,
        extractor: FactExtractor | None = None,
        embedder: EmbeddingProvider | None = None,
        canonicalizer: Canonicalizer | None = None,
        graph: GraphProjection | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.search = search
        self.extractor = extractor
        self.embedder = embedder
        self.canonicalizer = canonicalizer or Canonicalizer(
            threshold=settings.canonicalization_threshold, embedder=embedder
        )
        self.graph = graph

    # -- stage 1: ingest ----------------------------------------------------

    def ingest(self) -> IngestStats:
        """Read the vault and record everything that changed since last time."""
        vault = Vault(self.settings.vault_path)
        if not vault.exists():
            # Never create the vault here. If it lives on a USB stick or an
            # external drive that is not mounted, silently creating an empty
            # directory would report "your journal is empty" instead of "your
            # drive is missing", and then commit that emptiness to git.
            message = (
                f"The vault directory {self.settings.vault_path} does not exist. "
                f"Run diairy init to create it, or check that the drive holding "
                f"your journal is mounted."
            )
            raise VaultError(message)

        git = VaultGit(self.settings.vault_path)
        git.init()
        now = utc_now()
        commit = git.commit_all(f"diairy: snapshot {to_iso(now)}") or git.head_commit()

        run = Run(id=new_run_id(), started_at=now, pipeline_version=PIPELINE_VERSION)
        self.repository.start_run(run)
        stats = IngestStats(git_commit=commit)

        try:
            for source in vault.iter_files():
                stats.documents_seen += 1
                self._ingest_file(source, run_id=run.id, commit=commit, now=now, stats=stats)
        except Exception as exc:
            self.repository.finish_run(run.id, status="failed", error=str(exc)[:2000])
            raise

        self.repository.finish_run(run.id, status="completed", stats=stats.as_dict())
        return stats

    def _ingest_file(
        self,
        source: VaultFile,
        *,
        run_id: str,
        commit: str | None,
        now: datetime,
        stats: IngestStats,
    ) -> None:
        document_id = source.document_id
        version_id = document_version_id(document_id, source.content_hash)
        previous = self.repository.latest_version(document_id)
        if previous is not None and previous.id == version_id:
            return  # Unchanged since the last run. Nothing to do, and that is the point.

        parsed = parse_document(source.content)
        event_time = infer_event_time(source.relative_path, parsed.frontmatter, now)

        if previous is None:
            stats.documents_new += 1
            self.repository.upsert_document(
                Document(
                    id=document_id,
                    relative_path=source.relative_path,
                    first_seen_at=now,
                )
            )

        self.repository.insert_version(
            DocumentVersion(
                id=version_id,
                document_id=document_id,
                content_hash=source.content_hash,
                byte_size=source.byte_size,
                ingested_at=now,
                event_time=event_time,
                git_commit=commit,
            )
        )
        stats.versions_new += 1

        blocks = split_blocks(parsed.body, offset=parsed.body_offset)
        spans = chunk_blocks(
            source.content,
            blocks,
            target_chars=self.settings.chunk_target_chars,
            overlap_chars=self.settings.chunk_overlap_chars,
        )
        chunks = [
            Chunk(
                id=make_chunk_id(version_id, span.char_start, span.char_end),
                document_version_id=version_id,
                ordinal=ordinal,
                char_start=span.char_start,
                char_end=span.char_end,
                text=span.text,
                heading_path=span.heading_path,
            )
            for ordinal, span in enumerate(spans)
        ]
        stats.chunks_new += self.repository.insert_chunks(chunks)

        if previous is not None:
            # The file was edited. Its old facts stop being current, but they are
            # kept: they remain the truth of the version that produced them.
            stats.facts_superseded += self.repository.supersede_facts_of_version(
                previous.id, run_id
            )

    # -- stage 2: extract and canonicalise ----------------------------------

    def process(self, *, limit: int | None = None) -> ProcessStats:
        """Extract facts from every chunk that has none yet."""
        if self.extractor is None:
            message = "process() needs an extractor; none was configured."
            raise ValueError(message)

        pending = self.repository.chunks_without_facts(limit)
        stats = ProcessStats()
        if not pending:
            return stats

        run = Run(
            id=new_run_id(),
            started_at=utc_now(),
            model=self.extractor_model,
            profile=self.settings.model_profile,
            prompt_version=self.extractor.prompt_version,
            pipeline_version=PIPELINE_VERSION,
        )
        self.repository.start_run(run)
        stats.run_id = run.id

        types, predicates = self.repository.vocabulary()
        vocabulary = Vocabulary(types=types, predicates=predicates)
        known = self.repository.known_concepts(limit=KNOWN_CONCEPT_WINDOW)

        try:
            with RunLog(self.settings.runs_dir, run.id) as log:
                for item in pending:
                    stats.concepts_new += self._process_chunk(
                        item,
                        run_id=run.id,
                        vocabulary=vocabulary,
                        known=known,
                        log=log,
                        stats=stats,
                    )
        except Exception as exc:
            self.repository.finish_run(run.id, status="failed", error=str(exc)[:2000])
            raise

        stats.rejection_reasons = self.repository.rejection_counts(run.id)
        self.repository.finish_run(run.id, status="completed", stats=stats.as_dict())
        return stats

    @property
    def extractor_model(self) -> str:
        return "" if self.extractor is None else self.extractor.model

    def _process_chunk(
        self,
        item: PendingChunk,
        *,
        run_id: str,
        vocabulary: Vocabulary,
        known: list[CanonicalConcept],
        log: RunLog,
        stats: ProcessStats,
    ) -> int:
        if self.extractor is None:  # pragma: no cover -- guaranteed by process()
            message = "no extractor configured"
            raise ValueError(message)

        outcome = self.extractor.extract(
            item.chunk,
            run_id=run_id,
            event_time=item.event_time,
            vocabulary=vocabulary,
        )
        stats.chunks_processed += 1
        stats.facts_rejected += len(outcome.rejected)

        log.record_extraction(
            chunk_id=item.chunk.id,
            system="",  # already captured once per run; kept out of every entry
            prompt=self.extractor.build_prompt(
                item.chunk, vocabulary, item.event_time.date().isoformat()
            ),
            response=outcome.response.text if outcome.response else "",
            model=outcome.response.model if outcome.response else "",
            duration_ms=outcome.response.duration_ms if outcome.response else 0,
            accepted=len(outcome.facts),
            rejected=[(entry.reason, entry.detail) for entry in outcome.rejected],
        )
        self.repository.record_rejections(
            run_id,
            item.chunk.id,
            [(rejected.reason, rejected.detail) for rejected in outcome.rejected],
        )

        if not outcome.facts:
            return 0

        stats.facts_written += self.repository.insert_facts(outcome.facts)
        return self._canonicalise(outcome.facts, known=known)

    def _canonicalise(self, facts: list[Fact], *, known: list[CanonicalConcept]) -> int:
        """Resolve the concepts a batch of facts mentions, and record the mapping.

        Literal objects get concepts too. They stay unpromoted because nobody
        mentions the same literal twenty times, so they do not pollute the
        first-class vocabulary -- and keeping the treatment uniform means the
        graph can still show "rated 8/10" as an edge.
        """
        refs: list[ConceptRef] = []
        for fact in facts:
            refs.append(fact.subject)
            refs.append(fact.object)

        decisions = self.canonicalizer.resolve(refs, known)
        new_concepts = 0

        for index, fact in enumerate(facts):
            subject = decisions[index * 2]
            obj = decisions[index * 2 + 1]
            self.repository.set_fact_concepts(
                fact.id,
                subject_concept=subject.canonical_id,
                object_concept=obj.canonical_id,
            )
            for decision in (subject, obj):
                concept = CanonicalConcept(
                    id=decision.canonical_id,
                    label=decision.canonical_label,
                    type=decision.canonical_type,
                )
                self.repository.bump_concept(concept)
                alias = decision.to_alias()
                if alias is not None:
                    self.repository.upsert_alias(alias)
                if decision.is_new and all(item.id != concept.id for item in known):
                    known.append(concept)
                    new_concepts += 1
        return new_concepts

    # -- stage 3: embed -----------------------------------------------------

    def embed(self, *, batch_size: int = DEFAULT_EMBED_BATCH) -> EmbedStats:
        """Index every chunk that has no vector yet."""
        if self.embedder is None:
            return EmbedStats(available=False, note="no embedding provider configured")

        ready = self.search.ensure_vector_table(self.embedder.dimensions, self.embedder.model)
        if not ready:
            return EmbedStats(
                available=False,
                note="sqlite-vec could not be loaded; search will use full text only",
            )

        pending = self.search.unindexed_chunk_ids()
        embedded = 0
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            texts = self.repository.chunk_texts(batch)
            ordered = [(chunk, texts[chunk]) for chunk in batch if chunk in texts]
            if not ordered:
                continue
            vectors = self.embedder.embed([text for _, text in ordered])
            embedded += self.search.index_embeddings(
                [(chunk, vector) for (chunk, _), vector in zip(ordered, vectors, strict=True)]
            )
        return EmbedStats(chunks_embedded=embedded)

    # -- stage 4: project ---------------------------------------------------

    def project(self, *, rebuild: bool = False) -> ProjectionStats:
        """Write the current fact log into the graph.

        With ``rebuild``, the graph is dropped first. That is always safe: it
        holds nothing the fact log cannot regenerate.
        """
        if self.graph is None:
            return ProjectionStats()
        if rebuild:
            self.graph.drop()
        self.graph.connect()

        rows = self.repository.facts_for_projection()
        seen_concepts: set[str] = set()
        edges = 0
        for row in rows:
            for concept_id, label, type_name in (
                (row["subject_concept"], row["subject_label"], row["subject_type"]),
                (row["object_concept"], row["object_label"], row["object_type"]),
            ):
                if concept_id not in seen_concepts:
                    self.graph.upsert_concept(concept_id, label, type_name)
                    seen_concepts.add(concept_id)
            self.graph.upsert_edge(
                subject_id=row["subject_concept"],
                object_id=row["object_concept"],
                fact_id=row["fact_id"],
                predicate=row["predicate"],
                confidence=float(row["confidence"]),
                event_time=row["event_time"],
                chunk_id=row["chunk_id"],
            )
            edges += 1
        return ProjectionStats(concepts=len(seen_concepts), edges=edges)
