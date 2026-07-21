# ingest-pdf

A general-purpose PDF **digestion** tool: feed it any PDF and it emits a structured on-disk tree of *(page/segment image, transcribed-text)* pairs, with the directory shape adapting to the PDF (textbook chapters, exam questions, or plain pages).

## Language

**Ingest（消化 / 摄入）**:
The end-to-end process of turning one input PDF into a structured tree of Units on disk. The name of the whole tool and its single verb.
_Avoid_: import, parse, convert

**Unit（单元）**:
The atomic output element — one rendered **image file** paired with one **transcription** markdown file, colocated in the same directory. A Unit maps to a page, an exam question, a section, or a cropped region depending on the Segmentation Strategy.
_Avoid_: item, chunk, block, entry

**Transcription（转写）**:
The Markdown-plus-LaTeX text recognized from a Unit's image by the VLM. Same-language recognition of what the image shows, faithful to the source — **not** a cross-language rendering, and richer than plain text OCR (preserves headings, lists, tables, math).
_Avoid_: translation（翻译）, OCR, extraction

**Segmentation Strategy（切分策略）**:
The pluggable, content-derived rule that maps one PDF into a tree of Units — deciding *what* each Unit is (a page, a question, a section). It does **not** decide *where* Units land, nor — when a Layout Spec is present — *which* strategy runs for a given PDF; those are **Placement** concerns owned by the Layout Spec. Absent a spec, the tool auto-detects the strategy per PDF (ADR-0002/0006) and uses the variant's native default placement. The tool ships several variants (below).
_Avoid_: mode, parser, splitter

**Outline Strategy（章节切分）**:
The textbook path: pages grouped into a `第N章/<section>/` tree derived from **section numbers parsed out of each page's transcription** (e.g. `6.2.3 向量的数乘运算`), with heading-less pages inheriting the last-seen section; pages are the leaf Units. (The corpus has no PDF bookmarks and is half-scanned, and the VLM won't answer a direct heading question — ADR-0004 — so structure is harvested from the transcription, not a bookmark outline.)

**Question Strategy（试题切分）**:
Segmentation that splits an exam paper into per-question Units by detecting question boundaries. The exam path.

**Page Strategy（页面切分）**:
Segmentation with one whole page = one Unit, flat under a per-PDF directory. The universal fallback for a PDF with no exploitable structure.

**Region（区域 / 一页 N 题）**:
A sub-page crop: one page yielding N Unit images (e.g. several questions printed on one page). A refinement applied on top of the Question Strategy. Crop boundaries are reported by the VLM and snapped to the nearest blank horizontal band of the page image.

**Placement（落位）**:
*Where* a Unit's files land on disk and *how* they are named — a concern **orthogonal to Segmentation**. Each Segmentation Strategy carries a **native default** placement (Page → `<stem>/page-NNNN`, Outline → `<stem>/第N章/<section>/…`, Question → `<stem>/qNN`), used when no Layout Spec is present; a Layout Spec overrides it.
_Avoid_: layout (as a verb), path rule

**Layout Spec（布局规约）**:
A repo-owned, declarative file (`<repo>/.ingest/layout.yaml`) that is the **single source of truth** for Placement in the invoking repo. An **ordered list of rules**, each mapping a **filename pattern** (regex with named captures such as `year`/`region`/`subject`) to a **Segmentation Strategy** *and* a **destination path template** (mixing those captures with structural tokens the segmenter emits — `qno`, `page`, `section`). First match wins; a template's terminal token (`q{qno}`) is the Unit's filename prefix, the segmenter appends suffixes/extensions (`-stem`, `.png`, `.md`). Templates resolve from the **repo root** — the spec owns the destination, so `--out` is at most an override. The tool auto-discovers it by walking up from cwd. Because a consumer's taxonomy lives in the consumer's own spec, the tool itself stays consumer-agnostic (see ADR-0008).
_Avoid_: config, template, mapping file

**Manifest（清单）**:
The per-run record of every completed Unit and its provenance (source PDF, page, crop box, DPI, model id + revision). Drives idempotent per-Unit resume and serves as the audit trail.
_Avoid_: index, log, database

**Calibration（标定）**:
A per-model spot-check — sampling representative pages and checking their Transcriptions against the source images — that licenses trusting Transcriptions without per-page human review. Must be re-run whenever the model id/revision changes.
_Avoid_: validation, verification, testing
