"""The Segmentation Strategy protocol (CONTEXT: Segmentation Strategy).

A strategy maps a PDF into pipeline PageJobs, says where each page's full render
lands, and turns a page's VLM result into the Units to persist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import fitz

from ..models import OutUnit, PageJob, PageResult, RenderedPage


class Strategy(Protocol):
    name: str

    def plan(self, doc: "fitz.Document", pdf_path: Path, pdf_key: str, out_root: Path) -> list[PageJob]:
        """Enumerate the pages to process and where their Units live."""
        ...

    def render_target(self, job: PageJob) -> Path:
        """Where the render stage writes the full-page PNG for this job."""
        ...

    def emit(self, rendered: RenderedPage, result: PageResult) -> list[OutUnit]:
        """Turn one page's VLM result into the Units to persist."""
        ...
