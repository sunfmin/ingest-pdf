# One VLM call per page: combined transcription + boundary reporting

For a page holding N questions, the tool issues a **single** VLM call that returns both the page's full Markdown+LaTeX transcription **and** the per-question boundary boxes. The Markdown is then sliced at question numbers and the page image cropped at the boxes — no second recognition pass. Chosen because the single local GPU is the throughput ceiling (ADR-0001), so calls-per-page ≈ wall-clock; one call/page is N× cheaper than transcribing each crop separately.

## Considered options

- **One call per crop** — re-transcribe each question crop after locating it; cleaner per-question scoping but N× the GPU load, materially slower on dense pages.
- **Two passes (locate, then transcribe crops)** — most calls, slowest; only worth it if calibration shows combined locate+transcribe unreliable.

## Consequences

- Relies on the model doing transcription **and** grounding well in one shot — hence the grounding-capable default model (ADR-0001).
- Coarse boundary boxes are snapped to the nearest blank horizontal band before cropping, so imperfect coordinates still cut through whitespace rather than clipping a stem or figure.
- May be revisited per-strategy if a document type proves too dense for reliable single-shot output.
