# 6. Ollama as the default model runtime

Status: Accepted

## Context

One maintainer develops on an Apple Silicon Mac and can run real models. The
other is on a Windows laptop with no usable GPU. Both need to run the codebase,
and CI needs to run it on three platforms with no weights at all.

MLX is meaningfully faster on Apple Silicon, and Mac-only.

## Decision

Ollama is the default backend: one-step install on macOS and Windows, identical
HTTP API on both, loopback only.

Every model access goes through the `LLMProvider`, `EmbeddingProvider` and
`TranscriptionProvider` protocols in `diairy.nlp.provider`. No code above that
line knows which backend is answering.

Models are declared only in `diairy/nlp/data/registry.toml`, as profiles
(`small`, `medium`, `large`, `fake`), so each machine picks what it can run
while producing output against the same JSON schema.

## Alternatives considered

**MLX directly.** Faster on the M1, and it would leave one maintainer unable to
run the project. It remains the obvious next adapter, which the protocol makes
cheap.

**llama.cpp bindings.** More control, more build friction on Windows, and no
answer to model management.

**One model for everybody.** Would force either the 8 GB machine or the quality
bar to give way. Profiles let both hold.

## Consequences

- Both maintainers run the same code, and CI runs it on three platforms.
- Adding MLX later is one adapter and one registry entry.
- Ollama's HTTP overhead is irrelevant for a nightly batch.
- The `fake` profile makes the entire pipeline testable with no weights and no
  network, which is what CI runs.
- Ollama tags move. The registry is the single place to fix that.
