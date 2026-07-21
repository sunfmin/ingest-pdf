"""Placement (CONTEXT: Placement) — *where* one PDF's Units land + its scratch cache.

Placement is orthogonal to Segmentation: a strategy decides *what* a Unit is, this
decides *where* it goes. It is resolved **once per PDF by the pipeline** and handed to
the strategy's ``plan()``, so the "where" lives in a single seam instead of being
recomputed (``out_root / stem``) inside every strategy.

The default reproduces the historical layout exactly. A repo-owned Layout Spec
(``.ingest/layout.yaml``, ADR-0008) will override it here without touching any strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Placement:
    """Resolved destination for one PDF's Units.

    out_dir:   directory holding this PDF's Unit (image, md) pairs.
    cache_dir: per-PDF scratch base (e.g. the MinerU working dir for the Question path).
    """

    out_dir: Path
    cache_dir: Path


def resolve_placement(pdf_path: Path, out_root: Path) -> Placement:
    """The historical placement: Units under ``<out_root>/<stem>``, scratch under
    ``<out_root>/.mineru/<stem>``. The single point a Layout Spec will later override."""
    return Placement(
        out_dir=out_root / pdf_path.stem,
        cache_dir=out_root / ".mineru" / pdf_path.stem,
    )
