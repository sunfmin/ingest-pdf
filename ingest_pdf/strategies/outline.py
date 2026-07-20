"""Outline Strategy (CONTEXT / ADR-0004): the textbook path.

These textbooks have no bookmarks and are half-scanned, and the VLM won't answer
a heading question — but its transcription *contains* section headings. So Outline
transcribes into flat `page-NNNN` pairs (identical to the Page strategy) and then a
sequential **finalize** pass parses the section number out of each page's markdown,
carries the last-seen section forward onto heading-less pages, and reorganizes the
pairs into a `第N章/<section>/` tree.

Placement is deferred to finalize because a page's directory depends on its
transcription and on page order (carry-forward) — neither known at plan time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import strip_header
from .page import PageStrategy

if TYPE_CHECKING:
    from ..manifest import Manifest

# A section heading = a markdown heading whose text starts with N.N or N.N.N.
# (Only heading lines count — this ignores stray section-like numbers in body text.)
_SEC_HEADING = re.compile(r"^#{1,6}\s+(\d+)\.(\d+)(?:\.(\d+))?(?:\s+(.*\S))?\s*$")
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


class OutlineStrategy(PageStrategy):
    """Transcribes exactly like PageStrategy (flat page-NNNN pairs); the pipeline
    runs `finalize()` on the output dir afterwards to build the chapter/section tree."""

    name = "outline"


def section_of_page(md_body: str) -> tuple[int, str, str] | None:
    """First section-number heading on the page → (chapter, section_id, title), else None."""
    for line in md_body.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        m = _SEC_HEADING.match(s)
        if m:
            chapter = int(m.group(1))
            parts = [g for g in m.groups()[:3] if g]
            return chapter, ".".join(parts), (m.group(4) or "").strip()
    return None


def slug(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    return _ILLEGAL.sub("", text)[:40]


def _target_rel(cur: tuple[int, str, str] | None, unit_name: str) -> str:
    if cur is None:
        return f"front/{unit_name}"
    chapter, sid, title = cur
    section_dir = f"{sid}-{slug(title)}" if title else sid
    return f"第{chapter}章/{section_dir}/{unit_name}"


def finalize(out_dir: Path, manifest: "Manifest", pdf_key: str, log=print) -> None:
    """Reorganize a PDF's flat page-NNNN pairs into a 第N章/<section>/ tree.

    Idempotent + resume-safe: reads every done page's markdown in order (from its
    current on-disk location, flat or already-placed) to resolve carry-forward
    sections, then moves only the pairs not already at their target.
    """
    rec = manifest.data["pdfs"].get(pdf_key)
    if not rec:
        return
    page_idxs = sorted(int(k) for k, v in rec["pages"].items() if v.get("status") == "done")
    cur: tuple[int, str, str] | None = None
    moved = 0
    for idx in page_idxs:
        units = rec["pages"][str(idx)].get("units") or []
        if not units:
            continue
        u = units[0]
        stem = u.get("placed") or u["name"]  # relative to out_dir, no extension
        md_path = out_dir / f"{stem}.md"
        png_path = out_dir / f"{stem}.png"
        if not md_path.exists():
            log(f"  ! outline finalize: missing {md_path.name}; skipping p{idx + 1}")
            continue

        h = section_of_page(strip_header(md_path.read_text("utf-8")))
        if h:
            cur = h
        new_stem = _target_rel(cur, u["name"])
        if new_stem == stem:
            continue  # already placed correctly

        dest_md = out_dir / f"{new_stem}.md"
        dest_png = out_dir / f"{new_stem}.png"
        dest_md.parent.mkdir(parents=True, exist_ok=True)
        md_path.replace(dest_md)
        if png_path.exists():
            png_path.replace(dest_png)
        u["placed"] = new_stem
        u["md"] = f"{new_stem}.md"
        u["image"] = f"{new_stem}.png"
        moved += 1

    # prune now-empty flat dir residue is unnecessary (files were moved, not copied)
    manifest.save()
    log(f"  ✓ outline tree built for {out_dir.name}: {moved} page(s) placed, {len(page_idxs)} total")


# Exposed on the class so the pipeline's generic finalize collector (ADR-0006) can call it.
OutlineStrategy.finalize = staticmethod(finalize)
