"""The Segmentation Strategy protocol (CONTEXT: Segmentation Strategy).

A strategy maps a PDF into pipeline PageJobs, says where each page's full render
lands, and turns a rendered page into the Units to persist.

Every strategy transcribes via MinerU (the sole engine, ADR-0010): plan() runs MinerU
and holds its output, so emit() supplies the Units straight from the render — the
pipeline never runs a transcription stage.

Optional attributes (read by the pipeline via getattr):

    model_id:  str
    revision:  str
        The MinerU model that produced this strategy's segmentation + transcription,
        for provenance (ADR-0010). Every strategy sets these from _mineru.model_identity().
    finalize(out_dir, manifest, pdf_key, log) -> None   (module-level, optional)
        A whole-PDF post-pass run after the page loop (e.g. Outline's tree build,
        Question's cross-page assembly). The pipeline collects strategies that
        expose it and calls each once per PDF.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import fitz

from ..models import OutUnit, PageJob, RenderedPage
from ..placement import Placement


def strip_header(md: str) -> str:
    """Drop the leading provenance <!-- … --> comment."""
    if md.startswith("<!--"):
        end = md.find("-->")
        if end != -1:
            return md[end + 3 :].lstrip()
    return md


class Strategy(Protocol):
    name: str

    def plan(
        self, doc: "fitz.Document", pdf_path: Path, pdf_key: str, placement: Placement, pages=None
    ) -> list[PageJob]:
        """Enumerate the pages to process and where their Units live.

        ``placement`` (resolved once per PDF by the pipeline) supplies the destination
        directory + scratch cache, so the strategy no longer computes ``out_root/stem``.
        ``pages`` (a 1-based --pages filter, or None) lets a MinerU-backed strategy run the
        transcriber on only those pages instead of the whole PDF.
        """
        ...

    def render_target(self, job: PageJob) -> Path:
        """Where the render stage writes the full-page PNG for this job."""
        ...

    def emit(self, rendered: RenderedPage) -> list[OutUnit]:
        """Turn one rendered page into the Units to persist (transcription came from
        MinerU in plan(); this strategy holds it)."""
        ...
