"""VLM worker placeholder (ADR-0010).

MinerU is the sole transcription engine and every strategy is zero-VLM (needs_vlm=False),
so there is no in-process VLM. NoVLM is the sentinel the pipeline's provenance plumbing reads;
its transcribe() must never be called. (The mlx-vlm worker + NuExtract3 were retired in
ADR-0010, revising ADR-0001/0003/0005.)
"""

from __future__ import annotations

from ..models import PageResult, RenderedPage


class NoVLM:
    """Zero-VLM sentinel (ADR-0006/0010). Carries model_id/revision so the manifest's
    top-level provenance has something to read; transcribe() must never run because every
    strategy sets needs_vlm=False and the pipeline bypasses the VLM slot for them."""

    model_id = "none"
    revision = "n/a"

    def transcribe(self, rendered: RenderedPage) -> PageResult:  # pragma: no cover - guarded by needs_vlm
        raise RuntimeError("VLM transcribe invoked on a zero-VLM strategy run; this is a bug.")
