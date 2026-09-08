# 3. An append-only, bitemporal fact log

Status: Accepted

## Context

A journal describes a moving target. Opinions change, projects are abandoned,
people are reassessed. The interesting questions are precisely the ones about
that movement: what did I think of this book when I read it, which projects did
I drop, when did I stop caring about this.

A store that only holds the present cannot answer any of them.

There is also a second time axis, easy to miss: a note written on holiday and
synced a week later happened when it was written, not when we read it.

## Decision

The fact log is append-only. Facts are never deleted; a fact that stops being
current gets `superseded_by` set to the run that noticed, and the row stays
forever.

Every fact carries two timestamps: `event_time` (when it happened, taken from
frontmatter or the filename) and `knowledge_time` (when we learned it).

The vault is a git repository, giving the same property to the raw text.

## Alternatives considered

**Update rows in place.** Simple, and it silently destroys the history that
makes the product interesting.

**Soft deletes with a single timestamp.** Half the benefit. Without both time
axes, a backfilled note lands on the wrong day and every temporal query about it
is wrong.

**Event sourcing with a separate projection store.** The right idea at a much
larger scale. Here it is the same guarantee with more machinery.

## Consequences

- Time-travel queries are possible: the graph as it was on any date.
- The store grows monotonically. For one person's journal, this does not matter.
- Every read path must decide explicitly between current and historical facts.
  `current_only` is a required consideration, not a default to forget about.
- Migrations may only add. A migration that loses a row is a bug.
