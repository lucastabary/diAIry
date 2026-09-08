# 8. Model quotes are verified against the source

Status: Accepted

## Context

An extraction model will confidently produce claims the text does not support.
In a personal journal this failure is unusually costly: the system would be
quietly rewriting somebody's memory of their own life, in their own voice, with
no way to tell.

Confidence scores do not help. Models are confidently wrong.

## Decision

Every claim the model returns must include `quote`: a verbatim excerpt of the
source supporting it. The schema makes it mandatory, and constrained decoding
makes it impossible to omit.

We then locate that quote in the chunk ourselves, in three passes -- exact,
whitespace-insensitive, then case-insensitive. A quote we cannot find is treated
as fabricated, and the claim is dropped.

Located quotes are converted to absolute character offsets in the document, so
every stored fact points at the exact characters that justify it.

Rejections are counted and stored per run, not silently discarded.

## Alternatives considered

**Trust the model, filter on confidence.** Filters out honest uncertainty and
keeps confident invention. Exactly backwards.

**Ask the model for character offsets.** Models are poor at counting characters,
so this rejects true claims for arithmetic reasons.

**A second model verifying the first.** Doubles the cost, and adds a second
thing that can hallucinate.

**Fuzzy matching above a similarity threshold.** Considered, and deliberately
not done: a threshold is a knob that will be loosened whenever it is
inconvenient, and the whole value here is that the check is not negotiable.

## Consequences

- The most obvious hallucinations are removed deterministically, for free, with
  no model time.
- Every fact can be highlighted in the original file, which is what makes cited
  answers possible.
- Some true claims are lost: a correctly inferred fact whose quote the model
  paraphrases gets dropped. Accepted -- a lost fact is recoverable, an invented
  one is not.
- The rejection rate is the best single health signal for a model and prompt
  pair, and is recorded per run for exactly that reason.
