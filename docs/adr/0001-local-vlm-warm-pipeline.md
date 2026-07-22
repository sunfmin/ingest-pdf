# Local VLM with a warm-model single-GPU pipeline

> **Superseded by ADR-0010**: the local VLM was retired — MinerU is now the sole transcription engine and every strategy is zero-VLM. The warm-model single-GPU pipeline described here no longer has a VLM to keep warm; its VLM worker thread survives only as a vestigial passthrough. Read the rest as historical context.

> **Revised by ADR-0005**: the default transcription model is now `numind/NuExtract3-mlx-8bits`, not the Qwen3-VL-30B-4bit MoE. Calibration (14 pages × 3 rounds, scanned textbook + exam) found NuExtract3 more faithful and more stable, at 4.8 GB. Two premises below therefore no longer hold for the default: it is **not** "grounding-capable" (M4 boxes come from a separately-pinned Qwen3-VL call — ADR-0005), and it is a small ~4 GB model, not a 17 GB MoE. The warm-model single-GPU pipeline shape itself is unchanged.

Recognition runs on a **local mlx-vlm** rather than a cloud vision API — chosen for offline operation, zero per-page cost, and privacy, at the cost of raw speed and a per-model calibration burden. The default model is `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit`: a grounding-capable Mixture-of-Experts VLM with ~3B active params — fast on a single GPU yet near-30B in transcription quality, ~17 GB resident (fits a 48 GB M4 Pro with headroom). It is decoded at **low temperature (~0.2) with a repetition penalty (~1.05)** — smoke-tested under `mlx-vlm 0.6.5`, greedy/`temp=0` degenerates (immediate EOS or endless `$$` loops), so "faithful" here is *near*-deterministic, not greedy. The model id is swappable via `--model`; Calibration must be re-run on any change. Because a single Apple GPU serializes inference, "concurrency" is deliberately **not** parallel recognition: the model loads once and stays resident (warm) while parallel CPU workers render pages ahead into a bounded queue and parallel writers drain results — keeping the one GPU saturated. That warm-model pipeline is the sole source of speedup.

## Considered options

- **Cloud VLM API (Claude / Gemini)** — true parallel recognition and higher quality on dense CJK + math, rejected for the offline / zero-cost / privacy properties.
- **Multiple local model instances** — several VLM workers share one GPU, so they only time-slice and multiply memory use; no throughput gain, risk of OOM.

## Consequences

- Throughput is bounded by single-GPU inference; "fast" means *the GPU never idles*, not *N pages at once*. Smoke-tested ≈ 20–30 s/page generation at 200 dpi on the M4 Pro (~3.5 s one-time load), so a few pages/minute is the ceiling the pipeline works around, not beats.
- Switching engines later (cloud, or multi-GPU) re-opens the concurrency shape and requires re-running transcription calibration for the new model.
