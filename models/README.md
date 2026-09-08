# models/

This directory holds nothing that is committed.

The **model registry** — the single source of truth for which models diAIry can
use — lives with the code that reads it:

```
packages/diairy-nlp/src/diairy/nlp/data/registry.toml
```

It is packaged alongside the `diairy-nlp` distribution so an installed copy
works identically to a checkout. Never duplicate it here.

## What goes here

`models/weights/` is where you may keep downloaded weights if you want them
inside the project directory. It is gitignored, along with `*.gguf` and
`*.safetensors` anywhere in the tree. Weights are large, licensed separately,
and reproducibly downloadable — three good reasons never to commit them.

In practice you will not need this directory at all: Ollama manages its own
model store, and `ollama pull` is the only command involved.

## Getting the models for your profile

```bash
ollama pull qwen3:8b     # extraction, medium profile
ollama pull bge-m3       # embeddings, every profile
```

Check which models your profile wants:

```bash
uv run diairy status
```

## Changing a model

1. Edit `registry.toml`. Verify the tag exists with `ollama pull` first — Ollama
   tags move.
2. Run the evals: `uv run python evals/run_evals.py --profile <name>`.
3. Record the score in a comment above the profile, with the date.
4. Open a pull request explaining what improved and what regressed.

A model with no recorded eval score is a model nobody has measured.
