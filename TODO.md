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
- **Voice notes.** _Done (ADR 0009)._ `FasterWhisperTranscription` is wired
  behind `TranscriptionProvider`; the Write panel records in the browser
  (`MediaRecorder`), `POST /api/vault/transcribe` transcribes locally and keeps
  the audio under `attachments/`, and the transcript is saved as an entry whose
  `audio:` frontmatter links the recording. Weights come from `diairy models
  pull`. Still deferred:
  - **Orphan recordings.** The attachment is written as soon as transcription
    succeeds, so a recording the user then discards (never saves the entry)
    leaves an unreferenced blob under `attachments/`. Add a sweep that lists
    attachments no entry's `audio:` points at, and offer to remove them (respect
    the append-only spirit: propose, do not silently delete).
  - **Preloaded / warm model.** The whisper model loads on the first
    transcription of a server's life, so the first voice note is slow. Optionally
    warm it at `serve` startup, or keep a small model resident.
  - **Streaming transcription.** `transcribe` blocks until the whole clip is
    done. Stream partial text as it decodes for a live feel.
  - **Language hint.** Capture auto-detects the language, which is right by
    default; a small optional selector would help short or code-switched clips
    where detection is shaky (the API already accepts a `language` query param).
  - **Re-transcription.** The audio is kept precisely so a better model can
    redo a transcript later; nothing exposes that yet. A `diairy retranscribe`
    would supersede the old transcript's facts via the normal edit path.
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
- **Web UI.** A first read-and-explore UI landed as `diairy serve` (stdlib
  `http.server`, loopback only, no CDN assets). `--write` adds source entries to
  the vault (via `diairy.vault.writer.write_entry`, additive-only), `--dev`
  unlocks pipeline/graph/tests and implies `--write`. Still deferred:
  - **Editing existing entries from the UI.** The writer only creates new files.
    Revising an existing note in place would mean segmentation churn and needs a
    supersede-aware flow; keep it out until that is designed. For now the UI
    adds, and the user edits source in their own editor.
  - **Richer capture.** Voice notes now land through the writer (see "Voice
    notes" above). Still open: photo/file attachments through the same path.
  - **A `diairy new` / `diairy add` command.** The writer is UI-only; expose the
    same additive capture from the CLI for parity.
  - **Real-model `ask` over the web.** The API path exists but is only tested on
    the `fake` profile; exercise it against a real model and stream the answer
    (Server-Sent Events) instead of the current blocking POST.
  - **Live pipeline progress.** `POST /api/pipeline` blocks until a stage ends;
    stream per-chunk progress so a long overnight `process` is watchable.
  - **A real graph view.** `/api/neighbours` returns a text list; draw the
    one-hop neighbourhood, and add a timeline over `event_time`.
  - **Concept/vocabulary browser.** No page lists canonical concepts and their
    aliases yet; add one, linking each concept to its supporting passages.
  - **Auth for non-loopback binds.** `serve --host` beyond `127.0.0.1` is
    unauthenticated; require a token before allowing any non-loopback bind
    (especially now that `--write` and `--dev` can mutate the vault and store).
  - **ADR for the web surface.** Record why the UI lives in `diairy-cli` as
    `serve` (rather than a `diairy-web` package) and why it is stdlib-only.
  - **Real-model `ask` over the web.** The API path exists but is only tested on
    the `fake` profile; exercise it against a real model and stream the answer
    (Server-Sent Events) instead of the current blocking POST.
  - **Live pipeline progress.** `POST /api/pipeline` blocks until a stage ends;
    stream per-chunk progress so a long overnight `process` is watchable.
  - **A real graph view.** `/api/neighbours` returns a text list; draw the
    one-hop neighbourhood, and add a timeline over `event_time`.
  - **Concept/vocabulary browser.** No page lists canonical concepts and their
    aliases yet; add one, linking each concept to its supporting passages.
  - **Auth for non-loopback binds.** `serve --host` beyond `127.0.0.1` is
    unauthenticated; require a token before allowing any non-loopback bind.
  - **ADR for the web surface.** Record why the UI lives in `diairy-cli` as
    `serve` (rather than a `diairy-web` package) and why it is stdlib-only.

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
