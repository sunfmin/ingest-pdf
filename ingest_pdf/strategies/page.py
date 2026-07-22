"""Page Strategy (CONTEXT): one whole page = one Unit, flat under out_root/<pdf-stem>/.

Since ADR-0010 the transcription comes from **MinerU** — the sole recognition engine —
so Page is zero-VLM like Question (needs_vlm=False): MinerU transcribes the whole PDF once
in ``plan()`` and each page becomes one full-page ``(image, markdown)`` Unit. Page is the
universal fallback for a doc with no exploitable structure; Outline (its subclass) adds the
chapter/section-tree finalize on top of the identical per-page transcription.
"""

from __future__ import annotations

from pathlib import Path

from ..models import OutUnit, PageJob, PageResult, RenderedPage
from ..placement import Placement
from . import _mineru


class PageStrategy:
    """MinerU transcribes each page; one full-page Unit per page (ADR-0010)."""

    name = "page"
    needs_vlm = False

    def __init__(self) -> None:
        self.model_id, self.revision = _mineru.model_identity()
        self._md: dict[int, str] = {}  # page_index → transcription markdown

    def plan(self, doc, pdf_path: Path, pdf_key: str, placement: Placement) -> list[PageJob]:
        middle = _mineru.run_mineru(pdf_path, placement.cache_dir)
        self._md = _mineru.page_markdown(middle)
        out_dir = placement.out_dir
        return [
            PageJob(pdf_path=pdf_path, pdf_key=pdf_key, page_index=i, out_dir=out_dir)
            for i in range(doc.page_count)
        ]

    def render_target(self, job: PageJob) -> Path:
        return job.out_dir / f"page-{job.page_index + 1:04d}.png"

    def emit(self, rendered: RenderedPage, result: PageResult) -> list[OutUnit]:
        name = f"page-{rendered.job.page_index + 1:04d}"
        return [
            OutUnit(
                name=name,
                md_body=self._md.get(rendered.job.page_index, ""),
                image_name=f"{name}.png",
                source_page=rendered.job.page_index + 1,
            )
        ]
