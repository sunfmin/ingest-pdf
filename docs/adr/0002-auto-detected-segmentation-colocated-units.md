# Auto-detected pluggable segmentation, colocated Units in an external tree

The tool ships several **Segmentation Strategies** — Outline (textbook chapters, driven by the PDF bookmark outline), Question (exam papers, split per question), and Page (universal whole-page fallback) — and **auto-detects** which to apply per PDF: a bookmark outline present → Outline; no outline but question markers (`一、` / `17.` / `(1)`) detected → Question; else → Page. A `--strategy` flag overrides when the guess is wrong. Output is a tree of **Units**, each a colocated *(image file, transcription-markdown file)* pair, written to a user-specified `--out` directory — **never into the tool's own repo** (the prior pipeline committed products straight into the consuming wiki; this general tool must not couple to any one consumer). A per-run **Manifest** records completed Units for idempotent per-Unit resume.

## Considered options

- **Fixed single strategy** — rejected; the tool must digest arbitrary PDFs.
- **Explicit strategy only (no auto-detect)** — rejected; breaks the "throw any PDF at it" goal.
- **In-repo output** (like the prior wiki-embedded impl) — rejected; couples the tool to one consumer and bloats git history with binary page images.

## Consequences

- Auto-detection can misclassify; the `--strategy` override is the escape hatch, and detection is a cheap heuristic, not another VLM pass.
- Adding a new document type = adding a Strategy + a detection signal, nothing else.
