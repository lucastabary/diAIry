# 4. Open ontology, canonicalised afterwards

Status: Accepted

## Context

The obvious design is a closed ontology: Person, Place, Event, Book, Idea,
extended by migration. It is testable, queryable and stable.

It is also a decision, made in advance, about what a life contains. A journal
whose whole purpose is to be a catch-all -- work notes one week, an album being
composed the next -- will constantly hold things the schema did not anticipate,
and the schema will quietly flatten them.

The maintainers chose the open ontology explicitly, with the risk stated: left
alone, an open ontology produces `Personne`, `personne`, `person` and
`etre humain` as four unrelated types within a month, and a graph nobody can
query.

## Decision

The model names node types and predicates freely, in the language of the source.
Raw labels are stored verbatim and kept forever.

Queryability is recovered afterwards, by canonicalisation applied as a *view*
over the raw labels, never as a replacement:

1. **Vocabulary priming.** The terms already used in the graph are injected into
   the extraction prompt, so the model converges on its own past choices rather
   than inventing a new word each night.
2. **Three-pass resolution.** Exact match, then accent- and case-insensitive
   match, then embedding similarity above a threshold. Only the last costs a
   model call.
3. **Statistical promotion.** A type seen once stays marginal; a type seen often
   becomes first-class. The ontology emerges from actual use.
4. **Constrained decoding.** The *shape* of the answer is fixed by a JSON
   schema. Only the meaning is free.

## Alternatives considered

**Closed ontology.** Rejected by the maintainers, after the risks of the open
one were put to them twice. Recorded here so it is not proposed a third time.

**Closed core plus a proposal queue.** A genuine middle ground: fixed core
types, with model-invented types landing in a `proposed_types` table for manual
promotion. Rejected as still imposing a core.

**Full RDF with SPARQL.** Philosophically the right fit for open triples. Local
models write Cypher far more reliably than SPARQL, and the tooling is thinner.

## Consequences

- Nothing the user writes is forced into a category that does not fit.
- Canonicalisation can be re-run over all of history with a better model, a
  corrected alias or a different threshold, losing nothing.
- Query writing is harder: there is no fixed vocabulary to code against.
- Canonicalisation quality becomes the project's central quality problem. This
  is where evaluation effort should go.
- If the graph becomes unusable despite all this, the fallback is a closed core
  as a *view* over the same raw labels -- still without losing data.
