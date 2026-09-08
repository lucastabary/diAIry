# Evals

Extraction quality is not a unit test. It is non-deterministic, it needs model
weights, and it takes minutes. So it lives here, it runs on a machine that has a
model, and **it never runs in CI**.

The split is deliberate:

| | Tests (`packages/*/tests`) | Evals (here) |
|---|---|---|
| Question | does the code do what it says? | does the model understand French journalling? |
| Needs weights | never | always |
| Deterministic | yes | no |
| Blocks a PR | yes | no |
| Runs in CI | yes | never |

## Running

Needs Ollama running and the models for your profile pulled.

```bash
DIAIRY_MODEL_PROFILE=medium uv run python evals/run_evals.py
uv run python evals/run_evals.py --profile small --json report.json
```

## What it measures

- **Recall** — how many expected facts the model found. The headline number.
- **Rejection rate** — claims dropped because their quote was not in the source.
  A rise here means hallucination, and it is the single best health signal for a
  model and prompt pair.
- **Yield** — facts kept per chunk. Very low means the model is not engaging;
  very high usually means it is padding.
- **Duration** — how long a nightly batch would actually take on this machine.

Recall is deliberately generous: a fact counts as found when the expected
subject, predicate and object each appear as normalised substrings of what the
model produced. We are measuring whether it understood, not whether it guessed
our exact wording. An open ontology makes exact-match scoring meaningless.

## Fixtures

`fixtures/fr/*.md` — a synthetic French journal entry.
`fixtures/fr/*.expected.json` — the claims a competent reader should extract.

**Every fixture is invented.** No real journal content is ever committed, and a
pre-commit hook enforces it.

Writing a good fixture means including what actually appears in a journal and
breaks naive extraction: implicit subjects, abandoned sentences, mixed tenses,
anglicisms, a date in the filename and none in the text.

## Recording a score

When you add or change a model, run the evals and record the number in a comment
above its profile in `registry.toml`, with the date and the fixture count. A
score with no provenance is noise.

Expect roughly: `small` finds the explicit claims and misses the implicit ones,
`medium` is usable, `large` is noticeably better at inference and at entity
resolution. Measure it rather than trusting that sentence.
