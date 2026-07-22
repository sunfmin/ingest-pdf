"""Outline Strategy (CONTEXT / ADR-0004, ADR-0010): the textbook path.

These textbooks have no bookmarks and are half-scanned. Since ADR-0010 MinerU transcribes
every page (the sole engine), and its title blocks surface as Markdown `#` headings. So
Outline transcribes into flat `page-NNNN` pairs *exactly* like PageStrategy (it subclasses
it), then a sequential **finalize** pass parses the section number out of each page's
markdown, carries the last-seen section forward onto heading-less pages, and reorganizes the
pairs into a `第N章/<section>/` tree.

Placement is deferred to finalize because a page's directory depends on its transcription
and on page order (carry-forward) — neither known at plan time. When a doc has **no** section
headings at all (a non-textbook that auto-routing sent here, ADR-0010), finalize leaves the
pages flat — Outline degrades to Page rather than forcing an empty tree.
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
    """Same MinerU per-page transcription as PageStrategy (ADR-0010); the pipeline runs
    `finalize()` on the output dir afterwards to build the chapter/section tree (ADR-0004)."""

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

    Idempotent + resume-safe: reads every done page's markdown in order (from its current
    on-disk location, flat or already-placed) to resolve carry-forward sections, then moves
    only the pairs not already at their target. If **no** page carries a section heading, the
    doc is not a textbook (auto-routing sent it here); the pages are left flat (ADR-0010).
    """
    rec = manifest.data["pdfs"].get(pdf_key)
    if not rec:
        return
    page_idxs = sorted(int(k) for k, v in rec["pages"].items() if v.get("status") == "done")

    def _md_of(idx: int) -> tuple[str, Path] | None:
        units = rec["pages"][str(idx)].get("units") or []
        if not units:
            return None
        stem = units[0].get("placed") or units[0]["name"]  # relative to out_dir, no extension
        md_path = out_dir / f"{stem}.md"
        return (strip_header(md_path.read_text("utf-8")), md_path) if md_path.exists() else None

    # Graceful degrade: a doc with no section heading anywhere stays flat (Page-like).
    if not any((m := _md_of(i)) and section_of_page(m[0]) for i in page_idxs):
        log(f"  · outline finalize {out_dir.name}: no section headings — left flat ({len(page_idxs)} page(s))")
        return

    cur: tuple[int, str, str] | None = None
    moved = 0
    for idx in page_idxs:
        got = _md_of(idx)
        if got is None:
            log(f"  ! outline finalize: missing markdown for p{idx + 1}; skipping")
            continue
        body, md_path = got
        u = (rec["pages"][str(idx)].get("units") or [])[0]
        stem = u.get("placed") or u["name"]
        png_path = out_dir / f"{stem}.png"

        h = section_of_page(body)
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

    manifest.save()
    log(f"  ✓ outline tree built for {out_dir.name}: {moved} page(s) placed, {len(page_idxs)} total")


# Exposed on the class so the pipeline's generic finalize collector (ADR-0006) can call it.
OutlineStrategy.finalize = staticmethod(finalize)
