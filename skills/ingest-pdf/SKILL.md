---
name: ingest-pdf
description: >
  Digests one or many PDFs into a structured on-disk tree of (image, transcription) Units —
  textbooks (chapter/section tree), exam papers (per question, each as TWO images: with and
  without the worked solution), or plain pages. On invocation it analyzes every target PDF's
  structure, designs the matching output directory layout, shows you that plan once, then runs
  the zero-token ingest-pdf tool as a fast unattended batch (a warm MinerU server + cross-PDF
  parallelism) and verifies every Unit has its image+text pair. Use when the user says
  "digest/ingest these PDFs", "split this PDF into images and text", "turn these exam papers /
  textbooks / notes into a wiki-ready tree", "batch-process these PDFs", "extract questions /
  chapters from these PDFs", "/ingest-pdf", or otherwise points at PDF file(s)/dir(s) and wants
  structured (img, md) output on disk.
argument-hint: "<pdf-or-dir ...> [--out DIR] [--strategy auto|page|outline|question]"
allowed-tools: Read, Bash, Glob, Grep
---

# ingest-pdf — digest PDFs into a structured (image + text) tree

You orchestrate the **ingest-pdf** tool. The tool does all recognition/cutting with **zero
LLM tokens** (MinerU for exams, a local VLM for textbooks/pages); your job is to probe the
structure, present the directory plan once, run a fast batch, and verify. **Never** OCR or
transcribe pages yourself by reading images — that defeats the tool's design and is slow/costly.

## Inputs (parse from the user's message)

- **targets** — one or more PDF files and/or directories (the tool recurses dirs for `*.pdf`).
- **--out** — output root (default `./ingested` if the user didn't say). Each PDF lands under
  `<out>/<pdf-stem>/`.
- **--strategy** — `auto` (default) | `page` | `outline` | `question`. Use `auto` unless the
  user named a type ("these are exam papers" → `question`; "textbook" → `outline`).

## Locate the tool

```sh
REPO="${INGEST_PDF_REPO:-$HOME/Gaokao/ingest-pdf}"   # the ingest-pdf git clone (sunfmin/ingest-pdf)
```
All calls go through uv (auto-creates/uses the repo venv): `uv run --project "$REPO" ingest …`.
If `$REPO` doesn't exist, stop and tell the user (don't clone silently).

## Workflow

### A — Ensure readiness (one-time, only if needed)
Run the probe (B) first; if any row has `needs_mineru: true` **and** the isolated MinerU venv is
absent, install it once (idempotent; ~2 GB models via ModelScope; print a one-line notice, then
continue — the user chose unattended mode):
```sh
[ -x "$HOME/.cache/ingest-pdf/mineru-venv/bin/mineru" ] || uv run --project "$REPO" ingest --install-mineru
```

### B — Probe structure + design directories (cheap; no MinerU/VLM)
```sh
uv run --project "$REPO" ingest --inspect --strategy <s> <targets...>
```
stdout is a JSON array; each element =
`{path, pages, strategy, needs_mineru, needs_vlm, out_subdir, estimate}`.
- `strategy` is the resolved segmentation; `out_subdir` is the dir the Units go in (`<out>/<out_subdir>`).
- `estimate`: for `question` = detected question count on text-layer PDFs, or
  `"unknown (scanned)"` when there's no text layer (normal — MinerU resolves it at run time);
  for `outline` = a note that the chapter/section tree is resolved *after* transcription;
  for `page` = the page count.

Render this as a markdown table for the user **once** (do not ask per-PDF confirmation):
`| PDF | pages | strategy | estimate | needs mineru/vlm | dir |`.

### C — Warm the MinerU server (only if some row needs_mineru)
A warm server keeps models loaded across the whole batch (the per-PDF cold load is the slow
part). Start it in the background, wait for health, export its URL; the tool's runner then talks
to it over HTTP automatically. If it fails to start, warn and continue without the URL (the tool
falls back to per-PDF CLI — slower but works).
```sh
PORT="${MINERU_PORT:-8765}"
CFG="$HOME/.cache/ingest-pdf/mineru.json"   # written by --install-mineru (model-source=modelscope)
MINERU_TOOLS_CONFIG_JSON="$CFG" nohup "$HOME/.cache/ingest-pdf/mineru-venv/bin/mineru-api" \
    --host 127.0.0.1 --port "$PORT" > "$HOME/.cache/ingest-pdf/mineru-server.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 2; done
export MINERU_API_URL="http://127.0.0.1:$PORT"
```
Skip this block entirely if no row needs MinerU.

### D — Run the batch (one process; parallel inside)
```sh
uv run --project "$REPO" ingest <targets...> --out "$OUT" --strategy <s> \
    --concurrency "${INGEST_CONCURRENCY:-}"
```
The tool parallelizes across PDFs (planning + MinerU submits) and across pages (render/write)
within this single process, so one call saturates the machine — do **not** fan out multiple
`ingest` processes (they'd race on the shared manifest). Re-running resumes from the manifest.

### E — Verify
Read `<out>/manifest.json`: per PDF, count `done` vs `failed` pages. Cross-check on disk that
every recorded Unit has **both** files:
```sh
# for each unit dir, every *.md should have a same-named *.png (and vice versa)
```
Use Glob/Read; flag any Unit missing its pair or any `failed` page.

### F — Tear down
```sh
[ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null   # only if C started a server
```

## Output layout (what the tool produces, per strategy)
- **page** — `<out>/<stem>/page-NNNN.{png,md}` (flat).
- **outline** — `<out>/<stem>/第N章/<section>/page-NNNN.{png,md}` (tree resolved post-transcription).
- **question** — `<out>/<stem>/qNN.{png,md}` = full question (stem + options + solution), plus
  `<out>/<stem>/qNN-stem.{png,md}` = the question **without** the solution (cut just above
  `【答案】`/`【解析】`/`【分析】`/`【详解】`). A question whose PDF has no solution marker gets
  only `qNN` (no `-stem`) — that's expected, not an error.

Every `.md` carries a provenance header (`model@revision`, dpi, strategy, source page(s)).

## Report
End with a summary table: `| PDF | strategy | Units | ok | failed |` plus the output root path and
any caveats (scanned PDFs whose question count was unknown until run; questions without a stem
image; any failed pages with a suggested re-run).

## Boundaries / gotchas
- `auto` on a scanned PDF with no exploitable structure → `page`. The user can force `--strategy
  question` for scanned exam papers (MinerU handles them).
- The warm server (C) is what makes big batches fast; if you skipped it and the batch is slow,
  that's why — offer to re-run with it.
- Do not push, do not edit the lockfile, do not modify `~/.agents`/`~/.claude/skills` — this skill
  only *uses* the tool.
