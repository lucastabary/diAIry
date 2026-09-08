# TODO

Everything deliberately deferred. Nothing here is forgotten, and nothing here
is scheduled.

Rule: any idea raised and set aside lands here the moment it comes up, with
enough context to act on it months later. See `CLAUDE.md`.

---

## Capture

- **Live file watcher.** Ingestion currently scans the vault on demand, which
  suits a nightly batch. A `diairy watch` daemon would ingest on save. Needs
  debouncing, and must not fight the editor's atomic-write dance.
- **Voice notes.** `TranscriptionProvider` is defined and unimplemented. Wire
  `faster-whisper` (already named in the registry per profile), write the
  transcript into the vault as Markdown with frontmatter pointing at the audio
  file, and let the normal pipeline take it from there.
- **Photos and multimodality.** Either a vision model describing the image into
  Markdown, or true multimodal extraction. Decide with an ADR; the first is far
  cheaper and keeps the vault readable by humans.
- **URL ingestion.** Fetching a page contradicts the no-egress invariant. Needs
  an explicit, per-invocation, user-initiated exception with a clear UI — or a
  separate "archive this page" tool that runs outside the guarded process.
  ADR required before any code.
- **Local folder ingestion.** Point diAIry at a code or documents directory and
  let it index without copying anything into the vault. Read-only sources need
  a separate document kind, since they have no event time of their own.
- **Explicit tags and timestamps.** Capture is zero-friction on purpose today.
  Optional `#tags` and `HH:MM` markers are already recognised by the enrichment
  stage; what is missing is making them first-class in the graph and the UI.

## Privacy and security

- **Encryption at rest.** SQLCipher for the store, `age` for the vault, keys in
  the macOS Keychain and Windows DPAPI. Deferred while this is a prototype; the
  storage layer is already behind an interface so it can slot in.
- **Sharing between users, with strict controls.** Currently impossible by
  design, which is correct for now. Any future sharing needs per-concept opt-in,
  a preview of exactly what would leave, and an audit log. This is the feature
  most likely to quietly destroy the project's whole value proposition, so it
  gets an ADR and a threat model before a single line of code.
- **Multi-device sync.** Phone notes reaching the Mac. The vault being a git
  repository makes this mostly a plumbing problem (Syncthing, a private remote,
  iCloud), but conflict handling and the append-only invariant need thought.
- **Vault integrity check.** Detect a note modified outside git, or a store that
  has drifted from the vault it claims to describe.

## Extraction quality

- **Consolidation pass.** A second nightly pass that re-reads recent facts with
  the graph as context, merges duplicates and resolves entities the first pass
  could not ("Marc", "Marco", "mon collègue"). The batch schedule makes this
  affordable; it is the single biggest quality lever available.
- **Concept promotion.** `occurrence_count` and `promoted` exist on
  `canonical_concepts` and nothing sets `promoted` yet. Decide the threshold and
  what promotion actually changes (indexing, UI prominence, prompt priming).
- **Real language detection.** The stopword heuristic in `diairy.nlp.enrich` is
  deliberately tiny. Replace it behind the same signature when it starts costing
  quality.
- **Better French NER as a prior.** spaCy or similar, feeding candidates into
  the prompt. Deterministic, testable, and it reduces what the model has to
  guess.
- **Manual corrections.** A way to fix a wrong extraction that survives
  reprocessing. Probably a `corrections` table consulted after extraction, since
  editing facts directly would break the append-only invariant.
- **Rejection-rate alerting.** The rate is recorded per run. Nothing watches it.
  A sharp rise means a model or prompt regression and should be visible.

## Retrieval and use

- **Graph-aware retrieval.** Retrieval currently starts from passages and looks
  up their facts. The reverse — start from a concept, walk the graph, then fetch
  supporting passages — would answer "all the projects I abandoned" far better.
- **Time-travel queries.** The data is bitemporal and nothing exposes it yet.
  `diairy ask --as-of 2025-06` should answer with what was known then.
- **Periodic reports described in natural language.** The user writes "every
  Sunday, tell me what I read this week and what I said I would do", and that
  becomes a saved, scheduled query.
- **Discovery.** Suggest topics from the graph's shape alone. Optionally, and
  only with explicit consent, enrich against a local corpus (a Wikipedia dump,
  an arXiv mirror) — never a live service.
- **Web UI.** Read and explore first: timeline, graph view, provenance
  inspector. Localhost only, no CDN assets, no telemetry, bundled offline.

## Engineering

- **Cassette recording command.** `CassetteLLM` can replay but nothing records.
  Add `diairy record-cassette` so real model responses can be captured on the
  Mac and replayed in CI.
- **`diairy rebuild`.** Referenced in error messages, not implemented. Should
  rebuild the vector index, the graph, or everything, from the fact log or the
  vault.
- **MLX backend for Apple Silicon.** Faster than Ollama on an M1. The
  `LLMProvider` interface exists precisely so this is an adapter, not a
  refactor.
- **Incremental reprocessing on prompt change.** Prompt versions are recorded
  per fact but nothing uses them to select what to reprocess.
- **Per-package `CLAUDE.md`.** If the root file grows past comfortable reading,
  split the package-specific parts down into the packages.
- **Choose a licence.** `pyproject.toml` currently claims MIT with no `LICENSE`
  file. Decide and commit one, or remove the claim.
