# CLAUDE.md

Shared instructions for Claude Code on diAIry. Both maintainers work through
Claude, so this file is the contract that keeps two agents producing one
coherent codebase. Read it before touching anything.

---

## What this is

A journal you write freely into, which a local language model reads and turns
into a structured, queryable knowledge graph. Nothing leaves the machine. Ever.

The user writes Markdown in a vault directory. A nightly batch reads it,
extracts claims, canonicalises the concepts they mention, embeds the passages
and projects a graph. Then the user can ask questions and get answers that cite
their own words back at them.

---

## Rules that override your defaults

### Never commit without explicit approval

Do not run `git commit`, `git push`, or open a pull request unless the user
has asked for it **in that message**. "Looks good" on a diff is not approval
to commit. Prepare the change, describe it, and wait.

### Everything in English

Code, comments, docstrings, variable names, commit messages, PR titles and
bodies, documentation, ADRs, issue text. All English, always.

The exception is *content*: test fixtures and eval corpora are written in
French, because French is the maintainers' journalling language and the source
language we tune against. The tool itself must stay multilingual — never
hard-code a French assumption into the code.

### Every deferred idea goes into TODO.md

If you notice something worth doing but out of scope, or the user mentions an
idea "for later", add it to `TODO.md` before moving on. Do not leave it in the
conversation, and do not silently drop it. One line, under the right heading,
with enough context to act on months later.

This applies without being asked each time.

### Never delete data

There is no `DELETE` on a fact, ever. Facts that stop being true get
`superseded_by` set and stay in the table. The vault is a git repository, so
every revision of every note is recoverable. Migrations may only add.

Derived artefacts (the graph projection, the vector index) are the one
exception: they hold nothing the fact log cannot regenerate, and dropping them
is a normal operation.

### Never open a network connection

The egress guard in `diairy.core.egress` blocks every outbound connection
except loopback to the local model server. The test suite blocks even that. If
you need a dependency that phones home, you need a different dependency.

### Ask before adding a dependency

Every new package is new attack surface and one more thing that could contact
the network. Propose it, say why nothing in the standard library will do, and
wait for a decision.

---

## Working agreement

### Branches and pull requests

`main` is protected. No direct pushes, no exceptions.

```
<type>/<short-description>
```

`feat/graph-time-travel`, `fix/chunk-offset-drift`, `docs/adr-storage`,
`refactor/canonicaliser`, `test/pipeline-supersede`.

Every change goes through a pull request against `main`, with a description
that says *why*, not just what. CI must be green. One review.

### Conventional commits, mandatory

```
<type>(<scope>): <subject in the imperative>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`. Scope is the package without the `diairy-` prefix: `core`, `vault`,
`nlp`, `store`, `pipeline`, `query`, `cli`.

```
feat(nlp): prime extraction prompts with the existing vocabulary
fix(vault): keep chunk offsets absolute when frontmatter is present
```

Claude writes most of these. Get them right.

### Architecture decisions

Anything that would be expensive to reverse — a storage engine, a schema shape,
an ontology policy — gets an ADR in `docs/adr/` before the code lands. Copy the
format of the existing ones. Rejected options and their reasons matter more
than the chosen one.

---

## The invariants

These are not preferences. Code that breaks one of them is wrong, however
convenient it is.

1. **The Markdown vault is the only source of truth.** Everything else is
   derived and must be reconstructible from it. If you add state that cannot be
   rebuilt from the vault, you have created a second source of truth. Do not.
2. **Nothing is deleted.** See above.
3. **Every fact is bitemporal.** `event_time` (when it happened) and
   `knowledge_time` (when we learned it) are both required. Never collapse them.
4. **Every fact carries its provenance.** Chunk, absolute character offsets,
   verbatim quote, run id, model, prompt version, confidence. A fact that
   cannot be traced back to characters in a file is not a fact.
5. **No network egress.** See above.
6. **The model is the last resort, not the first.** If a regular expression, a
   parser or a SQL query can answer it, do that instead. Deterministic code is
   faster, free, and actually testable.

---

## Architecture

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

Packages, and the only direction dependencies may flow:

| Package | Owns | May import |
|---|---|---|
| `diairy-core` | domain model, ids, config, egress guard | nothing internal |
| `diairy-vault` | raw Markdown, git history, segmentation | core |
| `diairy-nlp` | providers, prompts, extraction, canonicalisation | core |
| `diairy-store` | SQLite fact log, search indexes, graph | core |
| `diairy-pipeline` | stage orchestration, run log | core, vault, nlp, store |
| `diairy-query` | retrieval, cited answers | core, nlp, store |
| `diairy-cli` | commands, wiring | all |

`diairy-core` must never grow a dependency on another `diairy-*` package. If
you feel the pull, the thing you are moving probably belongs in core itself.

`diairy` is a PEP 420 namespace package spread across all seven distributions.
Never add `src/diairy/__init__.py` — it would break the namespace and hide
every other package.

---

## Commands

```bash
uv sync --all-packages        # install everything, editable
uv run pytest                 # the whole suite
uv run ruff format .          # format
uv run ruff check . --fix     # lint
uv run mypy                   # type check, strict
uv run diairy --help          # the CLI
```

Before proposing a change as finished, all four of format, lint, mypy and
pytest must pass. That is exactly what CI runs.

---

## Testing

**Deterministic code gets real tests.** Parsing, segmentation, ids, storage,
canonicalisation, retrieval, the CLI. This is most of the codebase, and it is
held to a high standard.

**Model calls are never made in tests.** Use `ScriptedLLM`, `CassetteLLM` or
`HashingEmbeddings` from `diairy.nlp.fake`. The `fake` model profile exists for
exactly this. A test that needs the real thing is an eval, not a test.

**The network is blocked in every test** by an autouse fixture in the root
`conftest.py`. A test that genuinely needs loopback must be marked
`@pytest.mark.loopback`, which makes the exception visible.

**Never commit real journal content.** Fixtures are synthetic. A pre-commit
hook blocks `.db` files, run logs and anything that looks like a vault.

Assert on behaviour the user would notice, not on implementation details. Test
names are sentences: `test_editing_a_note_supersedes_old_facts_without_deleting_them`.

---

## Models

Every model lives in `packages/diairy-nlp/src/diairy/nlp/data/registry.toml`
and nowhere else. Never hard-code a model name anywhere in the codebase.

Adding one means: add a registry entry, run the evals, record the score in a
comment. Profiles (`small`, `medium`, `large`, `fake`) let each machine pick
what it can run while producing output against the same schema.

Prompts live in `packages/diairy-nlp/src/diairy/nlp/prompts/` and are versioned
by content hash — edit the text and the version changes automatically, which
marks the facts produced by the old wording as stale. Never version them by
hand.

---

## Style

- Python 3.12+, `from __future__ import annotations` everywhere.
- Full type annotations. mypy runs strict; `Any` needs a reason.
- `pathlib`, never `os.path`. Timezone-aware datetimes, never naive.
- Errors are raised with a message that says what to do next, not just what
  broke. Compare the messages in `diairy.core.errors` for the tone.
- Comments explain *why*. The code already says what.
- Docstrings on modules and public functions. Module docstrings explain the
  module's reason to exist, not its contents.
