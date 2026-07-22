"""The Segmentation Strategy protocol (CONTEXT: Segmentation Strategy).

A strategy maps a PDF into pipeline PageJobs, says where each page's full render
lands, and turns a page's VLM result into the Units to persist.

Every strategy transcribes via MinerU (the sole engine, ADR-0010); emit() supplies the
Units, so the pipeline never calls a VLM.

Optional attributes (read by the pipeline via getattr):

    model_id:  str | None = None
    revision:  str | None = None
        The model that produced this strategy's segmentation + transcription, for
        provenance. None → fall back to the NoVLM sentinel's ("none").
    finalize(out_dir, manifest, pdf_key, log) -> None   (module-level, optional)
        A whole-PDF post-pass run after the page loop (e.g. Outline's tree build,
        Question's cross-page assembly). The pipeline collects strategies that
        expose it and calls each once per PDF.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import fitz

from ..models import OutUnit, PageJob, PageResult, RenderedPage
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

    def emit(self, rendered: RenderedPage, result: PageResult) -> list[OutUnit]:
        """Turn one page's VLM result into the Units to persist."""
        ...
