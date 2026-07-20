"""Manifest per-PDF model field + staleness (ADR-0006, stage 1)."""

from __future__ import annotations

from ingest_pdf.manifest import Manifest


def _sig(size: int = 100, mtime: int = 1) -> dict:
    return {"size": size, "mtime": mtime}


def test_ensure_pdf_stores_per_pdf_model(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@abc")
    rec = m.data["pdfs"]["/p.pdf"]
    assert rec["model"] == "mineru@abc"
    assert rec["strategy"] == "question"


def test_ensure_pdf_default_model_is_none(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    m.ensure_pdf("/p.pdf", "page", _sig())
    assert m.data["pdfs"]["/p.pdf"]["model"] is None


def test_same_model_keeps_done_pages(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@abc")
    m.mark_page("/p.pdf", 0, "done", [{"name": "q01"}])
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@abc")  # re-run, unchanged
    assert m.page_done("/p.pdf", 0) is True


def test_model_change_resets_pages(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@abc")
    m.mark_page("/p.pdf", 0, "done", [{"name": "q01"}])
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@def")  # model changed → stale
    assert m.page_done("/p.pdf", 0) is False
    assert m.data["pdfs"]["/p.pdf"]["model"] == "mineru@def"


def test_strategy_change_resets_pages(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    m.ensure_pdf("/p.pdf", "question", _sig(), model="mineru@abc")
    m.mark_page("/p.pdf", 0, "done", [{"name": "q01"}])
    m.ensure_pdf("/p.pdf", "page", _sig(), model="mineru@abc")
    assert m.page_done("/p.pdf", 0) is False
