# Local VLM with a warm-model single-GPU pipeline

Recognition runs on a **local mlx-vlm** rather than a cloud vision API — chosen for offline operation, zero per-page cost, and privacy, at the cost of raw speed and a per-model calibration burden. The default model is `mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit`: a grounding-capable Mixture-of-Experts VLM with ~3B active params — fast on a single GPU yet near-30B in transcription quality, ~17 GB resident (fits a 48 GB M4 Pro with headroom). It is decoded at **low temperature (~0.2) with a repetition penalty (~1.05)** — smoke-tested under `mlx-vlm 0.6.5`, greedy/`temp=0` degenerates (immediate EOS or endless `$$` loops), so "faithful" here is *near*-deterministic, not greedy. The model id is swappable via `--model`; Calibration must be re-run on any change. Because a single Apple GPU serializes inference, "concurrency" is deliberately **not** parallel recognition: the model loads once and stays resident (warm) while parallel CPU workers render pages ahead into a bounded queue and parallel writers drain results — keeping the one GPU saturated. That warm-model pipeline is the sole source of speedup.

## Considered options

- **Cloud VLM API (Claude / Gemini)** — true parallel recognition and higher quality on dense CJK + math, rejected for the offline / zero-cost / privacy properties.
- **Multiple local model instances** — several VLM workers share one GPU, so they only time-slice and multiply memory use; no throughput gain, risk of OOM.

## Consequences

- Throughput is bounded by single-GPU inference; "fast" means *the GPU never idles*, not *N pages at once*. Smoke-tested ≈ 20–30 s/page generation at 200 dpi on the M4 Pro (~3.5 s one-time load), so a few pages/minute is the ceiling the pipeline works around, not beats.
- Switching engines later (cloud, or multi-GPU) re-opens the concurrency shape and requires re-running transcription calibration for the new model.
