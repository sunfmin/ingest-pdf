"""Core data types shared across the pipeline. See CONTEXT.md for the glossary."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in image pixels


@dataclass(frozen=True)
class Boundary:
    """A VLM-reported question boundary on a page (image-pixel coords, post scale-back)."""

    number: str
    box: Box


@dataclass
class PageJob:
    """One unit of pipeline work: render + transcribe a single PDF page.

    One VLM call per page (ADR-0003); a page may yield 1..N Units.
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
class PageResult:
    """VLM output for one page (ADR-0003: transcription + optional boundaries)."""

    markdown: str
    questions: list[Boundary] = field(default_factory=list)


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
