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
The pluggable rule that maps one PDF into a tree of Units and the directories that hold them. The tool ships several and selects one per PDF. Variants below.
_Avoid_: mode, parser, splitter

**Outline Strategy（章节切分）**:
Segmentation driven by the PDF's embedded bookmark outline (册→章→节); nested directories mirror the outline, pages are the leaf Units. The textbook path.

**Question Strategy（试题切分）**:
Segmentation that splits an exam paper into per-question Units by detecting question boundaries. The exam path.

**Page Strategy（页面切分）**:
Segmentation with one whole page = one Unit, flat under a per-PDF directory. The universal fallback for a PDF with no exploitable structure.

**Region（区域 / 一页 N 题）**:
A sub-page crop: one page yielding N Unit images (e.g. several questions printed on one page). A refinement applied on top of the Question Strategy. Crop boundaries are reported by the VLM and snapped to the nearest blank horizontal band of the page image.

**Manifest（清单）**:
The per-run record of every completed Unit and its provenance (source PDF, page, crop box, DPI, model id + revision). Drives idempotent per-Unit resume and serves as the audit trail.
_Avoid_: index, log, database

**Calibration（标定）**:
A per-model spot-check — sampling representative pages and checking their Transcriptions against the source images — that licenses trusting Transcriptions without per-page human review. Must be re-run whenever the model id/revision changes.
_Avoid_: validation, verification, testing
