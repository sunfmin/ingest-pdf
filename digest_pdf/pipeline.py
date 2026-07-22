"""The render→write pipeline (ADR-0001, simplified by ADR-0010).

Shape: a pool of render threads → a pool of writer threads, joined by one bounded queue.
Rendering (CPU) overlaps disk writes; the bounded write queue applies backpressure so we
don't render hundreds of pages ahead of the writers.

Transcription is owned by each strategy (MinerU, the sole engine — ADR-0006/0010): plan()
runs MinerU and holds its output, emit() supplies the Units from the render. There is no
in-process transcription stage — ADR-0001's warm-VLM worker thread was removed with the VLM
itself, so the pipeline is render→write. Provenance comes from the strategy's own model id.
"""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

import fitz

from .manifest import Manifest
from .models import RenderedPage, RunContext
from .placement import resolve_placement
from .provenance import header
from .render import render_page
from .strategies.detect import get_strategy

_SENTINEL = None
# A render failure is carried as a None RenderedPage on write_q (the writer marks that
# page failed); a successful render carries the RenderedPage that emit() turns into Units.


def _iter_pdfs(inputs: Iterable[Path]) -> list[Path]:
    pdfs: list[Path] = []
    for p in inputs:
        p = Path(p)
        if p.is_dir():
            pdfs.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf" and p.is_file():
            pdfs.append(p)
    # skip Office lock/temp files like "~$foo.pdf"
    return [p for p in pdfs if not p.name.startswith("~$")]


def run(
    inputs: Iterable[str | Path],
    out_root: str | Path,
    strategy_name: str,
    dpi: int = 200,
    n_render: int | None = None,
    n_writers: int = 4,
    pages: set[int] | None = None,
    log: Callable[[str], None] = print,
    spec=None,
) -> dict:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    n_render = n_render or max(2, (os.cpu_count() or 4) - 2)

    manifest = Manifest(out_root / "manifest.json")
    manifest.set_dpi(dpi)

    pdfs = _iter_pdfs([Path(p) for p in inputs])
    if not pdfs:
        log("no PDFs found.")
        return {"done": 0, "failed": 0, "skipped": 0}

    # ── Plan across all PDFs, in parallel (each job carries its resolved strategy) ──
    # Parallelism targets the per-PDF MinerU call inside plan() — the batch bottleneck for
    # the Question strategy. Safe because get_strategy() builds a fresh strategy instance
    # per PDF (so per-strategy state like _pages never crosses PDFs), ensure_pdf() locks
    # internally, and per-PDF output dirs / cache dirs are disjoint. With a warm MinerU
    # server (MINERU_API_URL) the parallel submits overlap against one model load.
    planned: list[tuple] = []  # (job, strat)
    finalize_targets: dict[str, tuple] = {}  # pdf_key -> (strat, out_dir); strategies with a finalize pass
    plan_lock = threading.Lock()

    def plan_pdf(pdf: Path) -> None:
        doc = fitz.open(pdf)
        try:
            # A matching Layout Spec rule pins both the strategy and the placement (ADR-0008);
            # otherwise fall back to the CLI strategy + historical <out_root>/<stem>.
            m = spec.match(pdf.stem) if spec is not None else None
            strat = get_strategy(m.rule.strategy if m else strategy_name, doc, pdf)
            pdf_key = str(pdf.resolve())
            placement = resolve_placement(pdf, out_root, m)  # the single 'where' seam (ADR-0008)
            # Per-PDF provenance model = "id@revision", the strategy's own MinerU model
            # (ADR-0006/0010). Including the revision makes a model upgrade invalidate pages.
            _mid, _mrev = strat.model_id, strat.revision
            manifest.ensure_pdf(pdf_key, strat.name, Manifest.source_sig(pdf), model=f"{_mid}@{_mrev}")
            local = []
            # pages is passed into plan() so MinerU-backed strategies transcribe only the
            # requested pages; the post-filter still guards any strategy that ignores it.
            for job in strat.plan(doc, pdf, pdf_key, placement, pages=pages):
                if pages and (job.page_index + 1) not in pages:
                    continue
                local.append((job, strat))
            with plan_lock:
                planned.extend(local)
                if hasattr(strat, "finalize"):
                    finalize_targets[pdf_key] = (strat, placement.out_dir)
        finally:
            doc.close()

    plan_workers = max(1, min(int(os.environ.get("DIGEST_PLAN_WORKERS", "3")), len(pdfs)))
    if plan_workers == 1:
        for pdf in pdfs:
            plan_pdf(pdf)
    else:
        with ThreadPoolExecutor(max_workers=plan_workers) as ex:
            list(ex.map(plan_pdf, pdfs))  # realize to surface any per-PDF plan error

    todo = [(j, s) for (j, s) in planned if not manifest.page_done(j.pdf_key, j.page_index)]
    skipped = len(planned) - len(todo)
    log(f"{len(pdfs)} PDF(s), {len(planned)} page(s) planned; {len(todo)} to do, {skipped} already done.")

    counters = {"done": 0, "failed": 0, "skipped": skipped}
    clock = threading.Lock()

    def _finalize() -> None:
        for pdf_key, (strat, odir) in finalize_targets.items():
            try:
                strat.finalize(odir, manifest, pdf_key, log=log)
            except Exception as e:
                log(f"  ✗ {getattr(strat, 'name', '?')} finalize {odir.name}: {e}")

    if not todo:
        _finalize()  # a fully-resumed run still (idempotently) runs each strategy's finalize
        return counters

    # ── Queues: render workers feed writers directly (no VLM stage — MinerU is the sole
    # transcriber, owned by each strategy's emit(); ADR-0010). write_q is bounded for backpressure.
    job_q: queue.Queue = queue.Queue()  # (job, strat) + render sentinels
    write_q: queue.Queue = queue.Queue(maxsize=64)  # (job, strat, RenderedPage|None) + writer sentinels

    for item in todo:
        job_q.put(item)
    for _ in range(n_render):
        job_q.put(_SENTINEL)

    def render_worker() -> None:
        while True:
            item = job_q.get()
            if item is _SENTINEL:
                return
            job, strat = item
            try:
                png = strat.render_target(job)
                w, h = render_page(job.pdf_path, job.page_index, dpi, png)
                write_q.put((job, strat, RenderedPage(job, png, w, h)))
            except Exception as e:  # keep the batch alive; the page is marked failed downstream
                write_q.put((job, strat, None))
                log(f"  ✗ render {job.pdf_path.name} p{job.page_index + 1}: {e}")

    def writer() -> None:
        while True:
            item = write_q.get()
            if item is _SENTINEL:
                return
            job, strat, rendered = item
            if rendered is None:  # render failed
                manifest.mark_page(job.pdf_key, job.page_index, "failed", [])
                with clock:
                    counters["failed"] += 1
                continue
            try:
                # Provenance per Unit = the strategy's own MinerU model (ADR-0006/0010).
                ctx = RunContext(dpi, strat.model_id, strat.revision, strat.name)
                recs = []
                for u in strat.emit(rendered):
                    md_path = job.out_dir / f"{u.name}.md"
                    md_path.parent.mkdir(parents=True, exist_ok=True)
                    md_path.write_text(header(ctx, job.pdf_path, u) + u.md_body, "utf-8")
                    recs.append(
                        {
                            "name": u.name,
                            "image": u.image_name,
                            "md": md_path.name,
                            "source_page": u.source_page,
                            "box": list(u.box) if u.box else None,
                        }
                    )
                manifest.mark_page(job.pdf_key, job.page_index, "done", recs)
                with clock:
                    counters["done"] += 1
                log(f"  ✓ {job.pdf_path.name} p{job.page_index + 1} → {', '.join(r['name'] for r in recs)}")
            except Exception as e:
                manifest.mark_page(job.pdf_key, job.page_index, "failed", [])
                with clock:
                    counters["failed"] += 1
                log(f"  ✗ write {job.pdf_path.name} p{job.page_index + 1}: {e}")

    renderers = [threading.Thread(target=render_worker, daemon=True, name=f"render-{i}") for i in range(n_render)]
    writers = [threading.Thread(target=writer, daemon=True, name=f"writer-{i}") for i in range(n_writers)]
    for t in (*renderers, *writers):
        t.start()

    try:
        for t in renderers:
            t.join()
        for _ in range(n_writers):
            write_q.put(_SENTINEL)  # renders done; tell writers to drain + close
        for t in writers:
            t.join()
        _finalize()  # outline tree (ADR-0004) / question cross-page assembly (ADR-0006)
    except KeyboardInterrupt:
        # Per-page manifest saves mean whatever finished is safely recorded; just re-run to resume.
        log("\ninterrupted — progress saved to manifest.json; re-run to resume.")
        raise

    return counters
