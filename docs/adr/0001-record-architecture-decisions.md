# 1. Record architecture decisions

Status: Accepted

## Context

Two maintainers, both working largely through Claude Code, on a project with
several genuinely contested technical choices: which graph engine, whether the
ontology is fixed, where the truth lives. Agents have no memory across
sessions, and humans have less than they think.

Without a written record, every one of these gets re-litigated. Worse, an agent
with no context will cheerfully "improve" a deliberate decision back into the
obvious default.

## Decision

Any decision that would be expensive to reverse gets an ADR in `docs/adr/`,
written and agreed before the implementation lands.

Expensive to reverse means: a storage engine, a schema shape, a policy that data
depends on, an invariant. Not: a library for parsing dates.

## Alternatives considered

**Comments in the code.** They explain local decisions well and cross-cutting
ones badly, and nobody finds them when the question comes up again.

**A wiki or a Notion page.** Splits the record from the code it describes, and
drifts. It also would not be in the repository, which is where an agent looks.

**Nothing, just talk about it.** This is the default, and it is why the
convention exists.

## Consequences

- Decisions are reviewable as pull requests, before the code exists.
- Claude reads `docs/adr/` and stops proposing options already rejected.
- Small overhead per decision. Accepted deliberately.
