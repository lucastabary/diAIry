# Contributing

Read [CLAUDE.md](CLAUDE.md) first. It is the working agreement, and it applies
to humans as much as to agents. This file is just the mechanics.

## Setup

```bash
uv sync --all-packages
uv run --with pre-commit pre-commit install
```

You need git on your PATH — the vault's history depends on it, and some tests
exercise it directly. Ollama is only needed for work that actually runs a model;
the whole test suite runs without it.

## The loop

```bash
uv run ruff format .
uv run ruff check . --fix
uv run mypy
uv run pytest
```

All four must pass before you open a pull request. CI runs exactly these, plus
the same suite on macOS and Windows.

## Branches

`main` is protected: no direct pushes, ever.

```
feat/graph-time-travel
fix/chunk-offset-drift
docs/adr-storage-choice
refactor/canonicaliser
test/pipeline-supersede
```

Branch from `main`, keep the branch focused on one thing, and rebase rather than
merge to keep the history readable.

## Commits and pull requests

Conventional commits, mandatory, in English:

```
feat(nlp): prime extraction prompts with the existing vocabulary
fix(vault): keep chunk offsets absolute when frontmatter is present
docs(adr): record why the graph is a projection
```

Types: `feat` `fix` `docs` `test` `refactor` `perf` `build` `ci` `chore`.
Scopes are package names without the prefix: `core` `vault` `nlp` `store`
`pipeline` `query` `cli`.

The PR title is checked by CI against the same pattern. The description should
explain **why**; the diff already says what.

## What CI enforces

| Job | What fails it |
|---|---|
| `lint` | unformatted code, or any ruff finding |
| `typecheck` | any mypy error, strict mode |
| `test` | a failing test on Linux, macOS or Windows |
| `patch coverage` | under 75% coverage on the lines you changed |
| `dependency allowlist` | a dependency nobody reviewed |
| `conventional commits` | a PR title that does not parse |

## Adding a dependency

Ask first — see CLAUDE.md. Once agreed: add it to the relevant
`pyproject.toml`, then add it to `ALLOWED` in `scripts/check_dependencies.py`
with a one-line reason. The CI job exists so that no package ever arrives
without someone having thought about whether it might phone home.

## Adding a model

Add it to `packages/diairy-nlp/src/diairy/nlp/data/registry.toml`, run the eval
suite against it, and record the score in a comment above the profile. Never
name a model anywhere else in the codebase.

## Architecture decisions

If a change would be expensive to reverse, write an ADR in `docs/adr/` first and
get it agreed before the implementation lands. Number it sequentially and follow
the format of the existing ones — the rejected options matter as much as the
chosen one.

## Testing rules

- Never call a real model. Use the fakes in `diairy.nlp.fake` and the `fake`
  profile.
- Never commit real journal content. Fixtures are synthetic and French.
- The network is blocked in every test. If you genuinely need loopback, mark the
  test `@pytest.mark.loopback` so the exception is visible.
- Name tests as sentences describing user-visible behaviour.

## Evals

Extraction quality is not a unit test — it is non-deterministic and needs
weights. It lives in `evals/`, runs on a machine that has a model, and is never
part of CI. See [evals/README.md](evals/README.md).
