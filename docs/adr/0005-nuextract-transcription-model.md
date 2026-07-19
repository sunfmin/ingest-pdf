# Transcription model: NuExtract3 (revises the default set in ADR-0001)

ADR-0001 set the default model to `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit`, chosen partly for its native **grounding** (for the ADR-0003 one-call transcription+boxes plan) and partly for near-30B transcription quality. Re-running Calibration on a wider corpus reverses the transcription half of that choice: the default transcription model is now **`numind/NuExtract3-mlx-8bits`** — an 8-bit, ~4 GB Qwen3.5-VL-based model fine-tuned by NuMind for structured extraction / image→Markdown. It is driven with the **same plain-Markdown transcription prompt** and the same low-temp+repetition-penalty decode (temp≈0.2 still required; temp=0 degenerates identically). Grounding is deliberately **no longer** a property of the default model (see below).

## Evidence (calibration: 14 pages × 3 independent rounds)

Corpus: 10 scanned textbook pages (必修一册 p13–22, spanning the 1.2→1.3 boundary) + the 4-page 2016 浙江 文科 exam (pages 1–3 have a text layer). Each model transcribed every page 3× with a fresh load.

| Metric | NuExtract3-8bit | Qwen3-VL-30B-4bit |
|---|---|---|
| Cross-round stability, textbook (mean difflib) | **0.968** | 0.813 (worst page 0.437) |
| Cross-round stability, exam | **0.954** | 0.722 (dense p1: **0.123**) |
| Exam CJK+digit recall vs text layer | **1.000** | 0.796 (dense p1: 0.543 — dropped ~46%) |
| Outline tree placement identical across rounds | **10/10** | 7/10 |
| Resident weights | **4.8 GB** | 17 GB |
| Wall-clock (2-page probe, incl. load) | ~58 s | ~50 s |

The 4-bit MoE's run-to-run variance (ADR-0001's "near-deterministic") is severe on dense math: it silently drops/swaps clauses, and its heading text wobbles (`集合间的基本关系` vs `集合的基本关系` — the「间」dropped), which makes the **Outline tree non-deterministic** (three pages land in two different section dirs across rounds). NuExtract3 is stable and reproduces the exam text losslessly. Earlier single-page visual inspection agreed: Qwen swapped set definitions (E/F), fabricated "3 不是长方形", and mangled the 真子集/空集 statements; NuExtract3 did not.

Caveat / scope: validated on **one** scanned textbook volume + **one** exam, 3 rounds. Other 人教A volumes, other subjects, and born-digital PDFs are not yet swept — residual calibration risk remains (Calibration must still be re-run per ADR-0001's rule when the corpus widens).

## Grounding: NuExtract3 does not replace Qwen3-VL for M4

Whether NuExtract3 could *also* emit boundary boxes (and collapse the two-model split) was tested empirically on the 8-question exam page, prompting in Qwen native grounding style (`bbox_2d` JSON), 4 prompt variants:

- Doc research: NuMind documents **no** grounding — no spatial field type, not advertised. The base (Qwen3.5-VL) grounds natively, and NuExtract3 **retains** Qwen's grounding special tokens (`<|box_start|>`, `<|quad_start|>`, `<|object_ref_*|>`, all `special=True`), so the capability is representable.
- Empirical: 2 of 4 prompts **reverted to full transcription** (the extraction fine-tune dominating); temp=0.2 never emitted boxes. The 2 that did emit `bbox_2d` JSON produced **fabricated** boxes — uniform ~180×29 slivers in a middle strip with scrambled labels, or one 743 px box + seven 14 px slivers — not real localizations. (The Qwen-30B reference with the same combined prompt also grounded poorly — 1 box for the whole page — consistent with ADR-0003's known unreliability of asking for boxes *and* text in one call.)

Verdict: NuExtract3 grounding is **unreliable as-is** and unusable for cropping. The extraction fine-tune degraded the base's grounding to fabrication/transcription-revert. So the model split stands:

- **Transcription** (Page, Outline, and the text side of the future Question strategy) → NuExtract3 (the default).
- **Grounding** (M4 per-question boxes) → a **separately-pinned Qwen3-VL** call, not the default worker.

## Consequences

- **ADR-0003 must split in M4.** Its "one VLM call returns transcription + boxes" premise is now doubly dead: the transcription model can't ground at all. M4 will run a NuExtract3 transcription call + a dedicated Qwen3-VL grounding call (two warm models, or a grounding worker loaded on demand). Record that split as its own ADR revising 0003 when M4 lands.
- **The default worker is no longer grounding-capable** — any future feature needing boxes must pin `--model` (or a dedicated grounding worker) to a Qwen-VL, never assume the default grounds.
- `--model` still swaps freely; Qwen3-VL-30B remains available (and is what M4 grounding will use).
- **Known minor residue, not fixed here:** both models keep the page footer/number despite the prompt asking to omit it (the footer is a plain line, not a `#` heading, so it does not affect the Outline tree — cosmetic). A robust footer stripper is deferred (regex stripping risks eating real content). Figure stripping already handles NuExtract3's `<figure><img alt=…>` output via the existing postprocess DOTALL rule — verified, no change needed.
