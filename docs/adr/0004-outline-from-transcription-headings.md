# Outline structure from transcription section numbers (textbooks lack bookmarks)

ADR-0002 said the Outline strategy would mirror the PDF's **bookmark outline**. Inspecting the real corpus killed that premise: none of the five 人教A版 volumes have any bookmarks (`get_toc()` empty), and three of five are pure scans with no text layer at all. A direct "which chapter/section is this page in?" query to the VLM **degenerates** on the 4-bit MoE (loops on `<|im_start|>`, emits nothing) — the same failure class as greedy decoding — while the plain transcription call works fine and *includes* section headings in its body (e.g. `6.2.3 向量的数乘运算`).

So the Outline strategy derives its tree from **section numbers parsed out of each page's transcription**, not from bookmarks or a heading query: parse the leading section number (`6.2.3` → chapter 6, section 6.2.3), carry the last-seen section forward onto heading-less pages (exercise / continuation pages), and build the `第N章/<section>/` directories in a cheap **sequential grouping pass** after the (still fully concurrent) transcription. This works uniformly on scanned and text-layer volumes because it rides on the transcription, which the VLM does reliably on both.

## Consequences

- Revises ADR-0002's Outline mechanism and its detection signal — "bookmark present → Outline" can never fire here, so detection (milestone 5) must key on something else (e.g. section-number density) or an explicit `--strategy outline`.
- A page's output directory is **not knowable at plan time** — it depends on its transcription — so Outline placement happens post-transcription; the GPU work stays parallel, only the dir-assignment pass is sequential and in page order (needed for carry-forward).
- Chapter/section **titles are best-effort**: present where printed on the page, otherwise a page falls back to `第N章` derived from the section number. Pages before the first heading land in a `front/` bucket.
- Not validated for every layout; if section numbers don't surface in the transcription for some book, that book degrades to flat pages rather than failing.
