"""Page Strategy (CONTEXT): one whole page = one Unit, flat under out_root/<pdf-stem>/.

Since ADR-0010 the transcription comes from **MinerU** — the sole recognition engine:
MinerU transcribes the whole PDF once in ``plan()`` and each page becomes one full-page
``(image, markdown)`` Unit. Page is the
universal fallback for a doc with no exploitable structure; Outline (its subclass) adds the
chapter/section-tree finalize on top of the identical per-page transcription.
"""

from __future__ import annotations

from pathlib import Path

from ..models import OutUnit, PageJob, RenderedPage
from ..placement import Placement
from . import _mineru


class PageStrategy:
    """MinerU transcribes each page; one full-page Unit per page (ADR-0010)."""

    name = "page"

    def __init__(self) -> None:
        self.model_id, self.revision = _mineru.model_identity()
        self._md: dict[int, str] = {}  # page_index → transcription markdown
        self._figs: dict[int, list[tuple[str, str]]] = {}  # page_index → [(dest, src)]
        self._images_dir: Path | None = None  # MinerU's images/ dir (figure sources)

    def plan(self, doc, pdf_path: Path, pdf_key: str, placement: Placement, pages=None) -> list[PageJob]:
        middle = _mineru.run_mineru(pdf_path, placement.cache_dir, pages=pages)
        self._md = _mineru.page_markdown(middle)
        self._figs = _mineru.page_figures(middle)
        self._images_dir = middle.parent / "images"
        out_dir = placement.out_dir
        return [
            PageJob(pdf_path=pdf_path, pdf_key=pdf_key, page_index=i, out_dir=out_dir)
            for i in range(doc.page_count)
        ]

    def render_target(self, job: PageJob) -> Path:
        return job.out_dir / f"page-{job.page_index + 1:04d}.png"

    def emit(self, rendered: RenderedPage) -> list[OutUnit]:
        pi = rendered.job.page_index
        name = f"page-{pi + 1:04d}"
        out_dir = rendered.job.out_dir
        # Copy this page's figures next to its md, under the page-scoped names page_markdown
        # already referenced (so `![](…)` resolves; outline.finalize moves them as a set).
        for dest, src in self._figs.get(pi, []):
            if self._images_dir is not None and (self._images_dir / src).exists():
                (out_dir / dest).write_bytes((self._images_dir / src).read_bytes())
        return [
            OutUnit(
                name=name,
                md_body=self._md.get(pi, ""),
                image_name=f"{name}.png",
                source_page=pi + 1,
            )
        ]
