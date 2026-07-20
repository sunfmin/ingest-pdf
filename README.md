# ingest-pdf

PDF ingestion for the Gaokao project.

## Agent skills

This repo is configured for Matt Pocock's engineering skills. Configuration lives in [`docs/agents/`](docs/agents/) — see [`CLAUDE.md`](CLAUDE.md) for the summary.

## Strategies

`ingest <pdf> --out <dir> [--strategy auto|page|outline|question]` (default `auto`).

- **page** — one whole page = one Unit (universal fallback).
- **outline** — textbook path; pages grouped into a `第N章/<section>/` tree from section
  numbers in the transcription (ADR-0004).
- **question** — exam path; MinerU splits the paper into per-question Units (cross-page
  questions reassembled), **zero VLM** — MinerU provides both segmentation and
  transcription (ADR-0006). MinerU is a heavy optional dependency kept out of the core
  venv; install it once before using this strategy:

  ```sh
  ingest --install-mineru      # builds an isolated venv + downloads models (ModelScope)
  ```

  or point `$MINERU_BIN` at an existing `mineru` executable.
