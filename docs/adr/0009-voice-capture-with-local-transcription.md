# 9. Voice capture with local transcription

Status: Accepted

## Context

Capture should be as close to zero-friction as writing. Speaking a note is the
lowest-friction capture there is, and it is also how we start gathering real
journal data to tune extraction against. So the web UI needs a "hold to speak,
get Markdown" button.

Speech-to-text is a model, and this project has one hard rule about models and
one about the network: every model is declared in `registry.toml`
(ADR 0006), and nothing leaves the machine (ADR 0007). Both bear directly on
how voice capture may be built.

`TranscriptionProvider` has existed in `diairy.nlp.provider` since the provider
protocols were written, and the registry has named a `faster-whisper`
transcription model per profile from the start. This ADR records the decisions
that turn that placeholder into a working feature.

## Decision

**Audio is captured in the browser, offline.** The frontend uses the
`MediaRecorder` API, which records to a local blob and touches no network. The
blob is POSTed to the loopback server as raw bytes.

**Recognition runs locally, through `TranscriptionProvider`.** The default
backend is `faster-whisper` (already in the registry). It auto-detects the
spoken language, so French and every other language work through the same path
with no per-language configuration. The provider is loaded behind the protocol,
exactly like the LLM and embedding backends, so an MLX or `whisper.cpp` adapter
is a later drop-in, not a rewrite.

**Weights are fetched once, outside the egress guard.** `faster-whisper`
downloads its weights from Hugging Face on first use — that is network egress,
and the guard blocks it, by design. So there is exactly one sanctioned way to
reach the network for this: `diairy models pull`, a command that deliberately
does **not** arm the egress guard, downloads the weights into `data_dir/models`,
and prints where they landed. This mirrors how Ollama models are pulled with
`ollama pull` — outside diAIry entirely. Every other code path, `serve`
included, loads the model with `local_files_only=True`: it reads local weights
or fails with an instruction to run the pull command. It can never download, and
even if a dependency tried, the guard is armed and would refuse.

**A voice note becomes two vault files, both additive.** The recording is saved
as an attachment under `attachments/` in the vault, and the transcript is saved
as a normal Markdown entry whose frontmatter carries `audio:` pointing at the
attachment. From there the ordinary pipeline takes over: the transcript is
segmented, extracted and embedded like anything typed. The audio is kept for
provenance and for re-transcription with a better model later. Neither write
ever overwrites (ADR 0002, and the append-only invariant): both go through the
vault writer, which only ever creates new files. The scanner ingests `*.md`
only, so audio blobs are never mistaken for source.

**Transcription and saving are two steps.** `POST /api/vault/transcribe` saves
the audio and returns the transcript; the frontend drops that text into the
editor so the user reviews and edits it before saving the entry. Whisper makes
mistakes, and a journal's source of truth should not be an unread transcript.

## Alternatives considered

**The browser's Web Speech API (`SpeechRecognition`).** Free, built in, and in
Chrome it streams the audio to Google's servers. That is precisely the thing
this project exists to never do. Rejected outright; it is not offered even as an
option.

**`openai-whisper` (the reference implementation).** Pulls in `torch`, a heavy
dependency, and is slower on CPU for the same result. `faster-whisper`
(CTranslate2) is faster and lighter on the Apple Silicon and CPU machines the
maintainers actually run.

**A local `whisper.cpp` HTTP server, added to the egress allowlist.** A clean
mirror of the Ollama arrangement and a real option, but another server for each
maintainer to install and supervise. Kept as a possible future adapter behind
the same protocol; not the default.

**Bundling the weights with the package.** Multi-gigabyte model files do not
belong in a git repository or a wheel. The pull command keeps the distribution
small and the download explicit.

**Transcribing straight into a saved entry.** Faster, but it would make the
vault's source of truth an unreviewed machine transcript. Review-before-save
keeps the human in the loop where it matters most.

## Consequences

- `faster-whisper` is an **optional** dependency (`diairy-nlp[transcription]`),
  so CI and a base install stay lean and model-free. The provider lazy-imports
  it and, if it is absent, fails with the command to install it.
- There is now exactly one command in the codebase that reaches the network on
  purpose. It is loud about it, it is user-initiated, and it is the documented
  exception the no-egress invariant already anticipated for model management.
- The vault gains an `attachments/` convention and an `audio:` frontmatter key.
  Both are additive and understood by humans reading the raw files.
- Editing a voice transcript before saving is the same flow as any typed note;
  the audio link is attached at save time.
- Deferred: capturing an orphaned recording when the user records but never
  saves (the attachment is written eagerly so the audio is never lost, which can
  leave an unreferenced file); a background/preloaded model so the first
  transcription is not slow; streaming partial transcripts. See `TODO.md`.
