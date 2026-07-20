"""The warm-model pipeline (ADR-0001).

Shape: a pool of render threads → a single VLM worker thread → a pool of writer
threads, joined by two bounded queues. The single VLM slot models the one-GPU
throughput ceiling; the bounded render→vlm queue applies backpressure so we
don't render hundreds of pages ahead of the model. Rendering and disk writes
overlap inference, keeping the (future real) GPU saturated.

Milestone 1 runs a StubVLM, so this proves the plumbing — resume, provenance,
colocated Unit pairs — without a model.
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
from .models import PageResult, RenderedPage, RunContext
from .provenance import header
from .render import render_page
from .strategies.detect import get_strategy

_SENTINEL = None
# A strategy with needs_vlm=False (e.g. Question/MinerU) owns its own segmentation +
# transcription, so the VLM slot is intentionally bypassed for its pages. This sentinel
# is distinct from None (which means the VLM call *failed*) so the writer records success.
_VLM_SKIP = object()
_EMPTY_PAGE_RESULT = PageResult(markdown="", questions=[])


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
    vlm,
    dpi: int = 200,
    n_render: int | None = None,
    n_writers: int = 4,
    pages: set[int] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    n_render = n_render or max(2, (os.cpu_count() or 4) - 2)

    manifest = Manifest(out_root / "manifest.json")
    manifest.set_model(vlm.model_id, vlm.revision, dpi)

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
            strat = get_strategy(strategy_name, doc, pdf)
            pdf_key = str(pdf.resolve())
            # Per-PDF provenance model = "id@revision": the strategy's own model when
            # it owns segmentation+transcription (Question/MinerU), else the VLM
            # (ADR-0006). Including the revision makes a model upgrade invalidate pages.
            _mid = getattr(strat, "model_id", None) or vlm.model_id
            _mrev = getattr(strat, "revision", None) or vlm.revision
            manifest.ensure_pdf(pdf_key, strat.name, Manifest.source_sig(pdf), model=f"{_mid}@{_mrev}")
            local = []
            for job in strat.plan(doc, pdf, pdf_key, out_root):
                if pages and (job.page_index + 1) not in pages:
                    continue
                local.append((job, strat))
            with plan_lock:
                planned.extend(local)
                if hasattr(strat, "finalize"):
                    finalize_targets[pdf_key] = (strat, out_root / pdf.stem)
        finally:
            doc.close()

    plan_workers = max(1, min(int(os.environ.get("INGEST_PLAN_WORKERS", "3")), len(pdfs)))
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

    # ── Queues ──
    job_q: queue.Queue = queue.Queue()  # (job, strat) + render sentinels
    render_q: queue.Queue = queue.Queue(maxsize=2 * n_render)  # (job, strat, RenderedPage|None); backpressure
    write_q: queue.Queue = queue.Queue(maxsize=64)  # (job, strat, rendered, result|None)

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
                render_q.put((job, strat, RenderedPage(job, png, w, h)))
            except Exception as e:  # keep the batch alive; the page is marked failed downstream
                render_q.put((job, strat, None))
                log(f"  ✗ render {job.pdf_path.name} p{job.page_index + 1}: {e}")

    def vlm_worker() -> None:
        while True:
            item = render_q.get()
            if item is _SENTINEL:
                for _ in range(n_writers):
                    write_q.put(_SENTINEL)
                return
            job, strat, rendered = item
            if rendered is None:
                write_q.put((job, strat, None, None))
                continue
            if not getattr(strat, "needs_vlm", True):
                write_q.put((job, strat, rendered, _VLM_SKIP))  # zero-VLM path (ADR-0006)
                continue
            try:
                result = vlm.transcribe(rendered)
                write_q.put((job, strat, rendered, result))
            except Exception as e:
                write_q.put((job, strat, rendered, None))
                log(f"  ✗ vlm {job.pdf_path.name} p{job.page_index + 1}: {e}")

    def writer() -> None:
        while True:
            item = write_q.get()
            if item is _SENTINEL:
                return
            job, strat, rendered, result = item
            if result is None:
                manifest.mark_page(job.pdf_key, job.page_index, "failed", [])
                with clock:
                    counters["failed"] += 1
                continue
            # _VLM_SKIP = success without a VLM result; the strategy supplies its own
            # data and ignores the (empty) PageResult we hand to emit.
            emit_result = _EMPTY_PAGE_RESULT if result is _VLM_SKIP else result
            try:
                # Provenance per Unit follows the strategy's model when it owns
                # segmentation+transcription (zero-VLM Question), else the VLM.
                ctx = RunContext(
                    dpi,
                    getattr(strat, "model_id", None) or vlm.model_id,
                    getattr(strat, "revision", None) or vlm.revision,
                    strat.name,
                )
                recs = []
                for u in strat.emit(rendered, emit_result):
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
    vlm_thread = threading.Thread(target=vlm_worker, daemon=True, name="vlm")
    writers = [threading.Thread(target=writer, daemon=True, name=f"writer-{i}") for i in range(n_writers)]
    for t in (*renderers, vlm_thread, *writers):
        t.start()

    try:
        for t in renderers:
            t.join()
        render_q.put(_SENTINEL)  # no more rendered pages; tell the VLM worker to drain + close
        vlm_thread.join()
        for t in writers:
            t.join()
        _finalize()  # outline tree (ADR-0004) / question cross-page assembly (ADR-0006)
    except KeyboardInterrupt:
        # Per-page manifest saves mean whatever finished is safely recorded; just re-run to resume.
        log("\ninterrupted — progress saved to manifest.json; re-run to resume.")
        raise

    return counters
