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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .layout import Match

# MinerU scratch base for a Layout-Spec-driven run — kept under .ingest/ so it stays
# out of the conforming output tree (真题/…), unlike the historical <out_root>/.mineru/.
_SPEC_CACHE_REL = Path(".ingest") / "cache"


@dataclass(frozen=True)
class Placement:
    """Resolved destination for one PDF's Units.

    out_dir:   directory holding this PDF's Unit (image, md) pairs.
    cache_dir: per-PDF scratch base (e.g. the MinerU working dir for the Question path).
    """

    out_dir: Path
    cache_dir: Path


def resolve_placement(pdf_path: Path, out_root: Path, match: "Optional[Match]" = None) -> Placement:
    """Resolve where one PDF's Units land — the single 'where' seam (ADR-0008).

    Without a Layout Spec match → the historical ``<out_root>/<stem>`` layout. With a
    match → the rule's template, resolved under ``out_root``: the template's directory
    portion (everything above the terminal leaf segment that carries the structural
    token) becomes the Unit directory. Leaf names stay the strategy's native form in v1,
    so the terminal token (``q{qno}`` etc.) declares granularity rather than renaming.
    MinerU scratch moves under ``<out_root>/.ingest/cache/<stem>`` to keep the conforming
    tree clean."""
    if match is not None:
        rel = match.resolve()  # e.g. "真题/浙江/2016/理/q{qno}"
        dest_dir = "/".join(rel.split("/")[:-1])  # drop the leaf segment → "真题/浙江/2016/理"
        return Placement(
            out_dir=out_root / dest_dir if dest_dir else out_root,
            cache_dir=out_root / _SPEC_CACHE_REL / pdf_path.stem,
        )
    return Placement(
        out_dir=out_root / pdf_path.stem,
        cache_dir=out_root / ".mineru" / pdf_path.stem,
    )
