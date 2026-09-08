# 2. The Markdown vault is the source of truth

Status: Accepted

## Context

The system holds years of somebody's private thinking. It also runs a pipeline
whose most important component -- a language model -- will certainly be replaced
several times, and whose prompts will change constantly.

If the extracted structure were the truth, every prompt change would risk
corrupting irreplaceable data, and improving the pipeline would become
frightening rather than routine.

## Decision

The vault of Markdown files is the only source of truth. Everything else -- the
SQLite fact log, the vector index, the graph -- is derived and must be fully
reconstructible from the vault alone.

Any state that cannot be rebuilt from the vault is a second source of truth, and
is not allowed.

## Alternatives considered

**Database-first, Markdown as an export.** Better for a rich editing UI, and
fatal here: it makes the user's own thinking hostage to our schema, and a
rebuild after a prompt change impossible.

**Both, synchronised.** Two sources of truth is zero sources of truth. The sync
bugs would be silent and permanent.

## Consequences

- Changing a prompt or a model is safe. Worst case, reprocess.
- The user can read, edit, back up and grep their journal with any tool, and
  will still be able to in twenty years.
- The vault is a git repository, so revisions are free.
- Reprocessing costs model time. Acceptable: this is a nightly batch.
- Extraction can never enrich the source file. It writes only to derived stores.
