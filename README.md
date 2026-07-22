# ingest-pdf

PDF ingestion for the Gaokao project.

## Agent skills

This repo is configured for Matt Pocock's engineering skills. Configuration lives in [`docs/agents/`](docs/agents/) — see [`CLAUDE.md`](CLAUDE.md) for the summary.

## Strategies

`ingest <pdf> --out <dir> [--strategy auto|page|outline|question]` (default `auto`).

**MinerU is the sole transcription engine** — every strategy runs it, **zero project VLM
tokens** (ADR-0010). `auto` routes exam papers to **question** and everything else to
**outline**.

- **outline** — textbook path (and auto's fallback); pages grouped into a `第N章/<section>/`
  tree from section numbers in the transcription (ADR-0004). A doc with no section headings
  degrades to flat `page-NNNN` pairs.
- **question** — exam path; MinerU splits the paper into per-question Units (cross-page
  questions reassembled), providing both segmentation and transcription (ADR-0006).
- **page** — one whole page = one Unit, flat; reached only via explicit `--strategy page`
  (or a Layout Spec rule) when a guaranteed-flat layout with no tree attempt is wanted.

Under the hood MinerU runs its bundled `MinerU2.5-Pro` VLM via the `hybrid-auto-engine`
backend, MLX-accelerated on Apple Silicon (ADR-0007). MinerU is a heavy dependency kept in
an isolated venv out of the core install; set it up once before ingesting anything:

```sh
ingest --install-mineru      # isolated venv + all models (ModelScope), mlx pinned
```

or point `$MINERU_BIN` at an existing `mineru` executable. Each Unit's provenance header
names the exact model, e.g. `MinerU2.5-Pro-2605-1.2B@mineru3.4.4-hybrid`.
