# diAIry

A journal you write freely into, that reads itself into a knowledge graph.
Entirely on your machine.

You write Markdown — ideas, what you did, what you want to do, half-formed
plans, things you read. A local language model reads it overnight, extracts what
it can, and builds a structured, queryable memory of your own life. Then you can
ask it questions, and every answer quotes your own words back at you with a date
and a filename.

**No data ever leaves your machine.** Not a byte. That is not a policy, it is
enforced: an egress guard blocks every outbound connection except the local
model server, and the test suite fails if any code path tries to open a socket.

## Why it might be different from the last one of these you saw

- **Your Markdown is the truth.** The database, the vector index and the graph
  are all derived, and all rebuildable. You can delete every one of them and
  lose nothing. Your notes stay plain files you can read in fifty years.
- **Nothing is ever deleted.** Edit a note and its old facts are marked
  superseded, not removed. The vault is a git repository. Every fact records
  both when it happened and when we learned it, so "what did I think of this
  book *when I read it*" is a question with an answer.
- **Every claim cites a character range.** The model must quote the sentence
  supporting each claim; we then locate that quote in your file ourselves and
  throw away anything we cannot find. Cheap, deterministic, and it kills the
  most obvious hallucinations for free.
- **The ontology is open.** No fixed schema of Person / Place / Event. The model
  names things in your language, and canonicalisation happens afterwards as a
  view — so it can be redone, corrected and improved without losing anything.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- git
- [Ollama](https://ollama.com), for anything involving a model

Runs on macOS (Apple Silicon included) and Windows. Model weights are never
committed and never downloaded by CI.

## Getting started

```bash
uv sync --all-packages
uv run diairy init
```

Then write something in the vault (`~/diAIry/vault` by default) and:

```bash
uv run diairy run
```

That is the whole nightly batch: ingest, extract, embed, project. It is designed
to be slow and thorough rather than interactive.

```bash
uv run diairy ask "qu'est-ce que je pensais de ce livre quand je l'ai lu ?"
uv run diairy search "roman"
uv run diairy status
uv run diairy inspect <chunk-id>
```

## Configuration

Everything is configurable by environment variable (`DIAIRY_*`) or a
`config.toml` in your OS config directory. The environment wins over the file;
explicit arguments win over both.

```toml
vault_path = "/Volumes/usb-stick/journal"   # anywhere: external drive, USB stick
model_profile = "medium"                    # small | medium | large
```

The vault can live wherever you want. Everything under `data_dir` is derived and
safe to delete.

## Model profiles

Each machine picks what it can run, and every profile must produce output valid
against the same schema:

| Profile | RAM | Extraction | Embeddings |
|---|---|---|---|
| `small` | 8 GB | Qwen3 4B | BGE-M3 |
| `medium` | 16 GB | Qwen3 8B | BGE-M3 |
| `large` | 32 GB | Qwen3 14B | BGE-M3 |
| `fake` | — | deterministic stub | hashed trigrams |

`fake` is what CI runs: the entire pipeline, end to end, with no weights and no
network. Models are declared in
`packages/diairy-nlp/src/diairy/nlp/data/registry.toml` and nowhere else.

## How it works

```
vault/*.md ──> ingest ──> parse ──> segment ──> enrich (no model)
                                                    │
                                                    v
                                              extract (LLM)
                                                    │
                                                    v
                              canonicalise ──> embed ──> project
                                       │           │        │
                                    SQLite     sqlite-vec  Kuzu
                                  (the truth)  (a view)  (a view)
```

Every stage is idempotent and resumable, and its work queue is derived from the
data itself ("chunks with no facts", "chunks with no vector") rather than from a
checkpoint that can go stale. Interrupting a run loses at most one chunk.

One SQLite file holds the fact log, the full-text index and the vectors. The
graph is an embedded Kuzu database. No servers, no Docker, no daemons.

## Contributing

Read [CLAUDE.md](CLAUDE.md) first — it is the working agreement, and it applies
to humans as much as to agents. Then [CONTRIBUTING.md](CONTRIBUTING.md) for the
mechanics, and [docs/adr/](docs/adr/) for why things are the way they are.

Deferred ideas live in [TODO.md](TODO.md).
