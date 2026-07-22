"""Core data types shared across the pipeline. See CONTEXT.md for the glossary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in image pixels


@dataclass
class PageJob:
    """One unit of pipeline work: render + transcribe a single PDF page.

    A page may yield 1..N Units (MinerU is the sole transcriber, ADR-0010).
    """

    pdf_path: Path
    pdf_key: str  # manifest key (resolved absolute path)
    page_index: int  # 0-based
    out_dir: Path  # directory holding this page's Unit(s)


@dataclass
class RenderedPage:
    job: PageJob
    png_path: Path  # full-page render (also the Page-strategy Unit image)
    width: int
    height: int


@dataclass
class OutUnit:
    """A Unit to persist: one (image, transcription) pair (CONTEXT: Unit).

    The provenance header is prepended by the writer, not here.
    """

    name: str  # base name, e.g. "page-0001" or "q17"
    md_body: str  # transcription markdown body
    image_name: str  # image filename within the job's out_dir
    source_page: int  # 1-based PDF page
    box: Optional[Box] = None


@dataclass
class RunContext:
    dpi: int
    model_id: str
    revision: str
    strategy: str
