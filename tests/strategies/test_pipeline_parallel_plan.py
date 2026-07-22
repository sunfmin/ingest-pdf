"""pipeline.run plans PDFs concurrently (skill's fast-batch enabler)."""

from __future__ import annotations

import json
import threading

import fitz

from ingest_pdf import pipeline
from ingest_pdf.strategies import _mineru as mu

_MIDDLE = {
    "pdf_info": [
        {
            "para_blocks": [
                {"bbox": [50, 80, 300, 100], "type": "text", "lines": [{"spans": [{"type": "text", "content": "一、x"}]}]},
                {"bbox": [50, 120, 300, 140], "type": "text", "lines": [{"spans": [{"type": "text", "content": "1. q"}]}]},
            ]
        }
    ]
}


class _V:
    model_id = "stub"
    revision = "m1"


def _mk(path):
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


def _fake_run_mineru_recording(barrier, entered):
    def fake(pdf, cache, log=None, pages=None):
        barrier.wait(timeout=10)  # both PDF threads must be here at once (serial ⇒ timeout ⇒ fail)
        entered.append(threading.get_ident())
        middle = cache / "m.json"
        middle.parent.mkdir(parents=True, exist_ok=True)
        middle.write_text(json.dumps(_MIDDLE))
        return middle

    return fake


def test_plan_runs_pdfs_concurrently(monkeypatch, tmp_path):
    monkeypatch.setenv("INGEST_PLAN_WORKERS", "2")
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _mk(a)
    _mk(b)
    barrier = threading.Barrier(2)
    entered: list[int] = []
    monkeypatch.setattr(mu, "run_mineru", _fake_run_mineru_recording(barrier, entered))

    counters = pipeline.run([a, b], tmp_path / "out", "question", _V(), log=lambda *_: None)

    assert counters["failed"] == 0 and counters["done"] == 2
    assert len(set(entered)) == 2  # two distinct threads reached run_mineru together


def test_plan_serial_branch_is_correct(monkeypatch, tmp_path):
    monkeypatch.setenv("INGEST_PLAN_WORKERS", "1")
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _mk(a)
    _mk(b)

    def fake(pdf, cache, log=None, pages=None):
        middle = cache / "m.json"
        middle.parent.mkdir(parents=True, exist_ok=True)
        middle.write_text(json.dumps(_MIDDLE))
        return middle

    monkeypatch.setattr(mu, "run_mineru", fake)

    counters = pipeline.run([a, b], tmp_path / "out", "question", _V(), log=lambda *_: None)
    assert counters == {"done": 2, "failed": 0, "skipped": 0}
