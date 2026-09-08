# Architecture decision records

One file per decision that would be expensive to reverse. Written before the
code lands, not after.

The rejected options are the valuable part. In six months nobody remembers what
we chose; what saves time is remembering what we already considered, and why we
said no.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-markdown-vault-is-the-source-of-truth.md) | The Markdown vault is the source of truth | Accepted |
| [0003](0003-append-only-bitemporal-fact-log.md) | An append-only, bitemporal fact log | Accepted |
| [0004](0004-open-ontology-with-post-hoc-canonicalisation.md) | Open ontology, canonicalised afterwards | Accepted |
| [0005](0005-embedded-storage-sqlite-and-kuzu.md) | Embedded storage: SQLite and Kuzu | Accepted |
| [0006](0006-ollama-as-the-default-model-runtime.md) | Ollama as the default model runtime | Accepted |
| [0007](0007-network-egress-is-blocked-and-tested.md) | Network egress is blocked, and tested | Accepted |
| [0008](0008-quotes-are-verified-against-the-source.md) | Model quotes are verified against the source | Accepted |

## Format

Context, Decision, Alternatives considered, Consequences. Numbered
sequentially. Statuses: Proposed, Accepted, Superseded by NNNN.

An ADR is never edited to change its decision. It gets superseded by a new one,
so the reasoning chain stays intact.
