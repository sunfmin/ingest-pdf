"""VLM worker interface + a milestone-1 stub.

Milestone 2 adds the real Qwen3-VL worker with the *same* interface
(`.model_id`, `.revision`, `.transcribe(rendered) -> PageResult`), decoded at
low temperature + repetition penalty (temp=0 degenerates — see ADR-0001), so
the pipeline needs no change to swap the stub out.
"""

from __future__ import annotations

from ..models import PageResult, RenderedPage


class StubVLM:
    """Returns fixed markdown so render → vlm → write → manifest → resume can be
    proven end-to-end without loading a model."""

    model_id = "stub"
    revision = "m1"

    def transcribe(self, rendered: RenderedPage) -> PageResult:
        job = rendered.job
        md = (
            f"# {rendered.png_path.stem}\n\n"
            f"_Stub transcription (milestone 1 — VLM not wired yet)._\n\n"
            f"- source page: {job.page_index + 1}\n"
            f"- image: `{rendered.png_path.name}` ({rendered.width}×{rendered.height})\n"
        )
        return PageResult(markdown=md, questions=[])
