---
name: digest-pdf
description: >
  Digests one or many PDFs into a structured on-disk tree of (image, transcription) Units —
  textbooks (chapter/section tree), exam papers (per question, each as TWO images: with and
  without the worked solution), or plain pages. On invocation it analyzes every target PDF's
  structure, confirms the invoking repo's output **Layout Spec** once (bootstrapping
  `.digest/layout.yaml` when it's absent, divergent, or a filename doesn't match), then runs
  the zero-token digest-pdf tool as a fast unattended batch (a warm MinerU server + cross-PDF
  parallelism) and verifies every Unit has its image+text pair. Use when the user says
  "digest/digest these PDFs", "split this PDF into images and text", "turn these exam papers /
  textbooks / notes into a wiki-ready tree", "batch-process these PDFs", "extract questions /
  chapters from these PDFs", "/digest-pdf", or otherwise points at PDF file(s)/dir(s) and wants
  structured (img, md) output on disk.
argument-hint: "<pdf-or-dir ...> [--out DIR] [--strategy auto|page|outline|question] [--layout FILE]"
allowed-tools: Read, Write, Bash, Glob, Grep
---

# digest-pdf — digest PDFs into a structured (image + text) tree

You orchestrate the **digest-pdf** tool. The tool does all recognition/cutting with **zero
LLM tokens** (MinerU for exams, a local VLM for textbooks/pages); your job is to understand the
invoking repo, confirm its **Layout Spec** (where Units land — CONTEXT: Layout Spec, ADR-0008)
once, run a fast batch, and verify. **Never** OCR or transcribe pages yourself by reading
images — that defeats the tool's design and is slow/costly.

## Inputs (parse from the user's message)

- **targets** — one or more PDF files and/or directories (the tool recurses dirs for `*.pdf`).
- **--out** — base override for the output root. Usually **omit it**: when the invoking repo
  has a Layout Spec, the tool lands each PDF per its `path` template resolved from the repo root.
  Pass `--out` only to write outside the repo (or on a no-spec run, where each PDF lands under
  `<out>/<pdf-stem>/`).
- **--strategy** — `auto` (default) | `page` | `outline` | `question`. A matching Layout Spec
  rule pins the strategy per PDF (overriding this); pass `--strategy` only on a no-spec run.
- **--layout** — path to a Layout Spec; defaults to auto-discovering `.digest/layout.yaml` by
  walking up from cwd.

## Locate the tool

```sh
REPO="${DIGEST_PDF_REPO:-$HOME/Gaokao/digest-pdf}"   # the digest-pdf git clone (sunfmin/digest-pdf)
```
All calls go through uv (auto-creates/uses the repo venv): `uv run --project "$REPO" digest …`.
If `$REPO` doesn't exist, stop and tell the user (don't clone silently).

## Workflow

### 0 — Understand the repo + confirm the Layout Spec (the gate)
The tool lands Units per a repo-owned **Layout Spec** (`<repo>/.digest/layout.yaml`, ADR-0008):
an ordered rule table, each rule mapping a **filename pattern** (regex + named captures like
year/region/subject) → a **segmentation strategy** + a **destination path template** (e.g.
`真题/{region}/{year}/{subject}/q{qno}`), resolved from the repo root. The spec is the one thing
you confirm — **once** — before an unattended batch. Steady-state runs (spec already matches)
are unattended; the gate only fires when the spec is absent, divergent, or incomplete.

1. Find the invoking repo (walk up from cwd for `.digest/`, else cwd); read its `CONTEXT.md`,
   if present, for naming conventions.
2. Probe + see matches in one cheap call (no MinerU/VLM):
   `uv run --project "$REPO" digest --inspect <targets...>`
   Each row now carries `layout: {status, rule, strategy, dest}`; `status` ∈
   `matched | unmatched | no-spec`.
3. Gate on the result:
   - **Every row `matched`, strategy unambiguous** → *proceed unattended*: show the plan table
     (B) **once** and run. Do **not** ask for per-PDF confirmation.
   - **`no-spec`, any `unmatched`, an ambiguous strategy, or a plan that diverges from the stored
     spec** → **STOP and confirm.** Propose a Layout Spec: derive filename-pattern rules from the
     target stems + the repo's conventions, pick each rule's strategy, write `path` templates that
     fit the repo. Present it as a YAML block, wait for the user to approve or adjust, then
     **write** `<repo>/.digest/layout.yaml` and re-run `--inspect`.
   - An **`unmatched`** stem → offer to add a rule that covers it; never fall back silently to the
     tool's native `<stem>/` layout.

### A — Ensure readiness (one-time, only if needed)
Using the Phase 0 `--inspect` rows: if any row has `needs_mineru: true` **and** the isolated
MinerU venv is absent, install it once (idempotent; ~2 GB models via ModelScope; print a one-line
notice, then continue — the layout is already confirmed):
```sh
[ -x "$HOME/.cache/digest-pdf/mineru-venv/bin/mineru" ] || uv run --project "$REPO" digest --install-mineru
```

### B — The `--inspect` row shape + plan table (reference for Phase 0)
Each element of the `--inspect` JSON array =
`{path, pages, strategy, needs_mineru, needs_vlm, out_subdir, estimate, layout}`.
- `strategy` is the resolved segmentation (a matching Layout Spec rule pins it); `layout` =
  `{status, rule, strategy, dest}`, `status` ∈ `matched | unmatched | no-spec`.
- `estimate`: for `question` = detected question count on text-layer PDFs, or
  `"unknown (scanned)"` when there's no text layer (normal — MinerU resolves it at run time);
  for `outline` = a note that the chapter/section tree is resolved *after* transcription;
  for `page` = the page count.

In Phase 0's unattended branch, render a markdown table for the user **once** (no per-PDF
confirmation once the spec is confirmed):
`| PDF | pages | strategy | dest | estimate | needs mineru/vlm |` — `dest` is the row's
`layout.dest` (the tool's native `<stem>/` when there is no spec).

### C — Warm the MinerU server (only if some row needs_mineru)
A warm server keeps models loaded across the whole batch (the per-PDF cold load is the slow
part). Start it in the background, wait for health, export its URL; the tool's runner then talks
to it over HTTP automatically. If it fails to start, warn and continue without the URL (the tool
falls back to per-PDF CLI — slower but works).
```sh
PORT="${MINERU_PORT:-8765}"
CFG="$HOME/.cache/digest-pdf/mineru.json"   # written by --install-mineru (model-source=modelscope)
MINERU_TOOLS_CONFIG_JSON="$CFG" nohup "$HOME/.cache/digest-pdf/mineru-venv/bin/mineru-api" \
    --host 127.0.0.1 --port "$PORT" > "$HOME/.cache/digest-pdf/mineru-server.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 2; done
export MINERU_API_URL="http://127.0.0.1:$PORT"
```
Skip this block entirely if no row needs MinerU.

### D — Run the batch (one process; parallel inside)
With a confirmed Layout Spec, **omit `--out` and `--strategy`** — the spec supplies the base
(repo root) and pins the strategy per PDF. Pass them only to override, or on a no-spec run.
```sh
uv run --project "$REPO" digest <targets...> --concurrency "${DIGEST_CONCURRENCY:-}"
# no-spec / override:  add  --out "$OUT" --strategy <s>
```
The tool parallelizes across PDFs (planning + MinerU submits) and across pages (render/write)
within this single process, so one call saturates the machine — do **not** fan out multiple
`digest` processes (they'd race on the shared manifest). Re-running resumes from the manifest.

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
The `<out>/<stem>/` prefix below is the **native (no-spec)** layout. With a matching Layout Spec
rule, that prefix is replaced by the rule's resolved `dest` directory (e.g. `真题/浙江/2016/理/`);
the leaf names (`page-NNNN`, `qNN`, the `第N章/<section>/` subtree) are unchanged in v1.
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
- The one file you may write is `<invoking-repo>/.digest/layout.yaml` (the Layout Spec bootstrap,
  Phase 0). Do **not** modify `~/.agents`/`~/.claude/skills` (the skill's own install), do not
  push, do not edit the lockfile — this skill only *uses* the tool.
