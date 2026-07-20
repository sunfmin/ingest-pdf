"""Question Strategy (CONTEXT / ADR-0006): split an exam paper into per-question Units.

Segmentation + transcription both come from MinerU (zero VLM on this path). plan() runs
MinerU once per PDF and groups its paragraph blocks into questions; emit() crops each
per-page fragment from the full-page render (box snapped to the nearest blank band).
Cross-page questions are reassembled by finalize().

Each question yields **two** Units (two images): the full question (stem + options +
solution) and the **stem** (cut just above the first solution marker — 【答案】, or
【解析】/【分析】/【详解】 when 【答案】 was not recognised). The stem variant is only
emitted when the question actually has a solution marker — otherwise the "without-solution"
image would equal the full one, so it is skipped.

Grouping is hardened against the failure the spike hit on a full 解析版 (missed Q11
because the model merged its header into the previous question's tail block): a question
number is recognised both at a block's start AND, as a fallback, when the *expected*
number appears after whitespace/newline inside a merged block. A pre-scan for a 大题头
(`一、…`) decides whether leading numbered lines (the 注意事项 list) must be gated out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
from PIL import Image

from .. import provenance
from ..models import OutUnit, PageJob, PageResult, RenderedPage
from . import _crop, _mineru
from ._mineru import MBlock
from .base import strip_header

if TYPE_CHECKING:
    from ..manifest import Manifest

# A 大题头 section header: "一、选择题 …". Used as the gating sentinel.
_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")
# Question header at a block's start; the trailing mark is optional (2024 Q4 = "4 已知").
_HEADER_RE = re.compile(r"^(\d{1,2})\s*[.．、]?\s")
# Same number appearing after whitespace/newline *inside* a merged block (fallback).
_MERGED_RE = re.compile(r"(?:^|[\s\n])(\d{1,2})\s*[.．、]\s")
# A solution-section marker. The stem ends just before the first of these. 【答案】 is the
# usual one, but MinerU sometimes drops it (observed: a question whose first solution block
# is 【分析】), so we also cut on 【解析】/【分析】/【详解】 — whichever comes first.
_SOLUTION_RE = re.compile(r"【(?:答案|解析|分析|详解)】")


@dataclass
class _Question:
    number: int
    blocks: list[tuple[int, MBlock]] = field(default_factory=list)  # (page_index, block)
    solution_start: int | None = None  # index into blocks of the first solution marker, else None


def _is_solution(b: MBlock) -> bool:
    return bool(_SOLUTION_RE.search(b.text))


def _block_text_stripped(b: MBlock) -> str:
    return b.text.strip()


def group_questions(stream: list[tuple[int, MBlock]], log=print) -> list[_Question]:
    """Group an in-reading-order (page, block) stream into questions (see module doc)."""
    has_section = any(_SECTION_RE.match(_block_text_stripped(b)) for _, b in stream)
    started = not has_section  # no 大题头 → begin at the first header, skipping a title/notice
    expected = 1
    questions: list[_Question] = []

    for pi, b in stream:
        t = _block_text_stripped(b)
        if not started:
            if _SECTION_RE.match(t):
                started = True
            continue  # preface (and the section header block itself) is never a question

        if _SECTION_RE.match(t):  # a later section header between questions — skip, don't attach
            continue

        m = _HEADER_RE.match(t)
        head = int(m.group(1)) if m else None
        if head is None:  # merged-block fallback: accept only the number we are expecting
            mm = _MERGED_RE.search(b.text)
            if mm and int(mm.group(1)) == expected:
                head = expected

        if head is None:
            if questions:
                cur = questions[-1]
                if cur.solution_start is None and _is_solution(b):
                    cur.solution_start = len(cur.blocks)
                cur.blocks.append((pi, b))
            continue

        if head == expected:
            questions.append(_Question(number=head, blocks=[(pi, b)]))
            expected += 1
        elif head > expected:
            log(f"  ! question: missing {expected}..{head - 1}, jumping to {head}")
            questions.append(_Question(number=head, blocks=[(pi, b)]))
            expected = head + 1
        else:  # head < expected → a stray body number (e.g. an option line) at block start
            if questions:
                questions[-1].blocks.append((pi, b))

    return questions


@dataclass
class _Frag:
    """One per-page fragment of a question variant (assembled into a Unit by finalize)."""

    number: int
    page: int
    box_pt: tuple[float, float, float, float]
    text: str
    variant: str  # "full" or "stem"


def _union_pt(blocks: list[MBlock]) -> tuple[float, float, float, float]:
    x0 = min(b.bbox[0] for b in blocks)
    y0 = min(b.bbox[1] for b in blocks)
    x1 = max(b.bbox[2] for b in blocks)
    y1 = max(b.bbox[3] for b in blocks)
    return (x0, y0, x1, y1)


def _build_frags(questions: list[_Question]) -> dict[int, list[_Frag]]:
    """questions → {page_index: [_Frag …]}; each question emits a full frag per page it
    touches and a stem frag per page that has pre-solution blocks (stem only if the
    question has a solution marker at all)."""
    pages: dict[int, list[_Frag]] = {}
    for q in questions:
        full_by_page: dict[int, list[MBlock]] = {}
        stem_by_page: dict[int, list[MBlock]] = {}
        for i, (pi, b) in enumerate(q.blocks):
            full_by_page.setdefault(pi, []).append(b)
            if q.solution_start is not None and i < q.solution_start:
                stem_by_page.setdefault(pi, []).append(b)
        for pi in sorted(full_by_page):
            pages.setdefault(pi, []).append(
                _Frag(q.number, pi, _union_pt(full_by_page[pi]), "".join(b.text for b in full_by_page[pi]), "full")
            )
            if q.solution_start is not None and stem_by_page.get(pi):
                sb = stem_by_page[pi]
                pages[pi].append(_Frag(q.number, pi, _union_pt(sb), "".join(b.text for b in sb), "stem"))
    return pages


def _frag_name(number: int, page_index: int, variant: str) -> str:
    suffix = "-stem" if variant == "stem" else ""
    return f"q{number:02d}{suffix}__p{page_index + 1:04d}"


class QuestionStrategy:
    """Exam path: MinerU segmentation + transcription, no VLM (ADR-0006)."""

    name = "question"
    needs_vlm = False

    def __init__(self) -> None:
        mid, rev = _mineru.model_identity()
        self.model_id = mid
        self.revision = rev
        self._pages: dict[int, list[_Frag]] = {}
        self._page_width: dict[int, float] = {}  # cache: page_index → width in points

    # ── Strategy protocol ────────────────────────────────────────────────────────

    def plan(self, doc: "fitz.Document", pdf_path: Path, pdf_key: str, out_root: Path) -> list[PageJob]:
        cache = out_root / ".mineru" / pdf_path.stem
        middle = _mineru.run_mineru(pdf_path, cache)
        per_page = _mineru.parse_blocks(middle)
        stream = [(pi, b) for pi in sorted(per_page) for b in per_page[pi]]
        questions = group_questions(stream)
        self._pages = _build_frags(questions)
        # Cache page widths from the already-open doc (avoid per-emit fitz.open, nit #2)
        self._page_width = {pi: float(doc[pi].rect.width) for pi in self._pages}
        out_dir = out_root / pdf_path.stem
        return [
            PageJob(pdf_path=pdf_path, pdf_key=pdf_key, page_index=pi, out_dir=out_dir)
            for pi in sorted(self._pages)
            if self._pages[pi]
        ]

    def render_target(self, job: PageJob) -> Path:
        return job.out_dir / ".renders" / f"page-{job.page_index + 1:04d}.png"

    def _page_width_pt(self, pdf_path: Path, page_index: int) -> float:
        """Memoized page width (pt). plan() warms the cache from the already-open doc,
        so in the pipeline this never reopens the PDF; the cold-open fallback only fires
        when emit() is driven without plan() (e.g. unit tests)."""
        w = self._page_width.get(page_index)
        if w is None:
            doc = fitz.open(pdf_path)
            try:
                w = float(doc[page_index].rect.width)
            finally:
                doc.close()
            self._page_width[page_index] = w
        return w

    def emit(self, rendered: RenderedPage, result: PageResult) -> list[OutUnit]:
        frags = self._pages.get(rendered.job.page_index, [])
        if not frags:
            return []

        zoom = rendered.width / self._page_width_pt(rendered.job.pdf_path, rendered.job.page_index)
        full = Image.open(rendered.png_path).convert("RGB")
        gray = full.convert("L")
        blank = _crop.blank_rows(gray)

        units: list[OutUnit] = []
        for f in frags:
            box_px = (f.box_pt[0] * zoom, f.box_pt[1] * zoom, f.box_pt[2] * zoom, f.box_pt[3] * zoom)
            # stem bottom is the cut just above the solution → don't snap it downward into it
            snapped = _crop.snap(box_px, blank, snap_bottom=(f.variant != "stem"))
            if snapped[3] <= snapped[1]:
                continue
            crop = _crop.crop_box(full, snapped)
            name = _frag_name(f.number, rendered.job.page_index, f.variant)
            image_name = f"{name}.png"
            (rendered.job.out_dir / image_name).write_bytes(_crop.png_bytes(crop))
            units.append(
                OutUnit(
                    name=name,
                    md_body=f.text,
                    image_name=image_name,
                    source_page=rendered.job.page_index + 1,
                    box=snapped,
                )
            )
        return units


# ── cross-page assembly (finalize) ──────────────────────────────────────────────

_FRAG_RE = re.compile(r"^q(\d+)(-stem)?__p\d+$")
_FINAL_RE = re.compile(r"^q(\d+)(-stem)?$")


def _qnum(name: str) -> int | None:
    m = _FRAG_RE.match(name) or _FINAL_RE.match(name)
    return int(m.group(1)) if m else None


def _is_stem(name: str) -> bool:
    return "-stem" in name


def finalize(out_dir: Path, manifest: "Manifest", pdf_key: str, log=print) -> None:
    """Assemble per-page fragments into one Unit per (question, variant).

    Idempotent + resume-safe (mirrors outline.finalize, ADR-0004): only fragment-named
    Units (qNN[-stem]__pPPPP) are merged; already-assembled qNN / qNN-stem are left
    untouched, so a re-run after finalize is a no-op. Each merged Unit is recorded under
    its first page; fragment image/md files and the intermediate .renders/ are removed.
    """
    import shutil

    rec = manifest.data["pdfs"].get(pdf_key)
    if not rec:
        return
    dpi = manifest.data.get("model", {}).get("dpi", 0)
    model = rec.get("model", "unknown")
    strategy = rec.get("strategy", "question")
    pdf_name = Path(pdf_key).name

    page_idxs = sorted(int(k) for k, v in rec["pages"].items() if v.get("status") == "done")
    groups: dict[tuple[int, str], list[tuple[int, dict]]] = {}
    for pi in page_idxs:
        for u in rec["pages"][str(pi)].get("units") or []:
            if _FRAG_RE.match(u["name"]):
                key = (_qnum(u["name"]), "stem" if _is_stem(u["name"]) else "full")
                groups.setdefault(key, []).append((pi, u))

    new_units: dict[int, list[dict]] = {
        pi: [u for u in (rec["pages"][str(pi)].get("units") or []) if not _FRAG_RE.match(u["name"])]
        for pi in page_idxs
    }

    merged = 0
    for (qnum, variant) in sorted(groups):
        frags = groups[(qnum, variant)]
        first_pi = frags[0][0]
        pngs, bodies, pages = [], [], []
        for pi, u in frags:
            p_png = out_dir / u["image"]
            if p_png.exists():
                pngs.append(p_png.read_bytes())
            p_md = out_dir / u["md"]
            if p_md.exists():
                bodies.append(strip_header(p_md.read_text("utf-8")))
            pages.append(pi + 1)
        canon = f"q{qnum:02d}" + ("-stem" if variant == "stem" else "")
        if pngs:
            (out_dir / f"{canon}.png").write_bytes(_crop.concat_vertical(pngs))
        (out_dir / f"{canon}.md").write_text(
            provenance.merged_header(model, dpi, strategy, pdf_name, pages) + "\n\n".join(bodies),
            "utf-8",
        )
        for _, u in frags:
            for key in ("image", "md"):
                fp = out_dir / u[key]
                if fp.exists():
                    fp.unlink()
        new_units[first_pi].append(
            {"name": canon, "image": f"{canon}.png", "md": f"{canon}.md", "source_page": first_pi + 1, "box": None}
        )
        merged += 1

    for pi in page_idxs:
        rec["pages"][str(pi)]["units"] = new_units[pi]

    renders = out_dir / ".renders"
    if renders.exists():
        shutil.rmtree(renders, ignore_errors=True)
    manifest.save()
    log(
        f"  ✓ question finalize {out_dir.name}: {merged} unit(s) assembled "
        f"(full + stem, cross-page merged where needed); fragments + .renders cleaned"
    )


QuestionStrategy.finalize = staticmethod(finalize)
