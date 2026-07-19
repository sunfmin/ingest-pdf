"""Self-describing provenance header prepended to every Unit's markdown.

Carried over from the prior impl's habit: each .md says how it was produced.
"""

from __future__ import annotations

from pathlib import Path

from .models import OutUnit, RunContext


def header(ctx: RunContext, pdf_path: Path, unit: OutUnit) -> str:
    box = f" · box {list(unit.box)}" if unit.box else ""
    return (
        f"<!-- ingest-pdf · {ctx.model_id}@{ctx.revision} · {ctx.dpi}dpi · strategy {ctx.strategy}\n"
        f"     source: {pdf_path.name} · page {unit.source_page}{box} -->\n\n"
    )
