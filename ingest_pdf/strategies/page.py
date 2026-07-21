"""Page Strategy (CONTEXT): one whole page = one Unit, flat under out_root/<pdf-stem>/.

The universal fallback, and the only strategy wired in milestone 1.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from ..models import OutUnit, PageJob, PageResult, RenderedPage
from ..placement import Placement


class PageStrategy:
    name = "page"

    def plan(self, doc: "fitz.Document", pdf_path: Path, pdf_key: str, placement: Placement) -> list[PageJob]:
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
                md_body=result.markdown,
                image_name=f"{name}.png",
                source_page=rendered.job.page_index + 1,
            )
        ]
