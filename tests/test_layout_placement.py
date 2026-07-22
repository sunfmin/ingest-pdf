"""Layout Spec applied to placement (ADR-0008, #14): Units land at the templated path,
relative to the repo root; --out overrides; unmatched PDFs fall back to native layout.
Every strategy transcribes via MinerU now (ADR-0010), so the CLI tests monkeypatch it —
synthetic PDFs + hand-written middle.json, no models."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import fitz

from digest_pdf import layout, pipeline
from digest_pdf.cli import main
from digest_pdf.placement import resolve_placement
from digest_pdf.strategies import _mineru

QSPEC = r"""
rules:
  - name: 浙江高考数学真题
    match: '(?P<year>\d{4})年(?P<region>浙江)高考数学(?:【(?P<subject>[理文])】)?'
    strategy: question
    path: '真题/{region}/{year}/{subject}/q{qno}'
"""

PSPEC = """
rules:
  - {name: notes, match: 'notes', strategy: page, path: 'raw/{page}'}
"""


def _write_spec(root: Path, text: str) -> Path:
    d = root / ".digest"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "layout.yaml"
    p.write_text(text, "utf-8")
    return p


class _FakeVLM:
    model_id = "stub"
    revision = "m1"

    def __init__(self) -> None:
        self.transcribe = Mock()


def _para(text, bbox):
    return {"bbox": bbox, "type": "text", "lines": [{"spans": [{"type": "text", "content": text}]}]}


def _one_question_middle(path: Path) -> None:
    data = {
        "pdf_info": [
            {
                "para_blocks": [
                    _para("一、选择题：本题共 1 小题", [50, 40, 300, 60]),
                    _para("1. 第一题题干", [50, 80, 350, 100]),
                    _para("【答案】A", [50, 120, 350, 140]),
                ]
            }
        ]
    }
    path.write_text(json.dumps(data), "utf-8")


def _prose_middle(path: Path) -> None:
    """A 1-page middle.json with a single text block (no section heading) — a page/outline
    PDF's MinerU output, so the CLI placement tests need no real MinerU (ADR-0010)."""
    data = {"pdf_info": [{"para_blocks": [_para("just some prose, not an exam", [50, 40, 350, 60])]}]}
    path.write_text(json.dumps(data), "utf-8")


def _patch_mineru(monkeypatch, middle: Path) -> None:
    monkeypatch.setattr(_mineru, "run_mineru", lambda p, cache, log=print, pages=None: middle)
    monkeypatch.setattr(_mineru, "model_identity", lambda: ("mineru", "test"))


def _blank_pdf(path: Path, n=1, w=400, h=500) -> None:
    doc = fitz.open()
    for _ in range(n):
        doc.new_page(width=w, height=h)
    doc.save(path)
    doc.close()


def _prose_pdf(path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "just some prose, not an exam")
    doc.save(path)
    doc.close()


# ── unit: resolve_placement ──────────────────────────────────────────────────


def test_resolve_placement_default_is_historical(tmp_path):
    pl = resolve_placement(Path("/x/2016.pdf"), tmp_path)
    assert pl.out_dir == tmp_path / "2016"
    assert pl.cache_dir == tmp_path / ".mineru" / "2016"


def test_resolve_placement_with_match_uses_template_dir_and_digest_cache(tmp_path):
    spec = layout.load_spec(explicit=_write_spec(tmp_path, QSPEC))
    stem = "2016年浙江高考数学【理】（解析版）"
    m = spec.match(stem)
    pl = resolve_placement(Path(f"x/{stem}.pdf"), tmp_path, m)
    assert pl.out_dir == tmp_path / "真题" / "浙江" / "2016" / "理"
    assert pl.cache_dir == tmp_path / ".digest" / "cache" / stem


# ── pipeline: question lands at templated path ───────────────────────────────


def test_question_pipeline_lands_at_templated_path(tmp_path, monkeypatch):
    spec = layout.load_spec(explicit=_write_spec(tmp_path, QSPEC))
    pdf = tmp_path / "2016年浙江高考数学【理】（解析版）.pdf"
    _blank_pdf(pdf)
    middle = tmp_path / "mid.json"
    _one_question_middle(middle)
    monkeypatch.setattr(_mineru, "run_mineru", lambda p, cache, log=print, pages=None: middle)
    monkeypatch.setattr(_mineru, "model_identity", lambda: ("mineru", "test"))

    base = tmp_path / "repo"
    counters = pipeline.run([pdf], base, "auto", _FakeVLM(), log=lambda *_: None, spec=spec)

    assert counters["failed"] == 0 and counters["done"] == 1
    unit_dir = base / "真题" / "浙江" / "2016" / "理"
    assert (unit_dir / "q01.png").exists() and (unit_dir / "q01.md").exists()
    assert (unit_dir / "q01-stem.png").exists()
    assert (base / "manifest.json").exists()
    # native <stem> dir is NOT used when a rule matched
    assert not (base / "2016年浙江高考数学【理】（解析版）").exists()


# ── CLI: page, no --out → repo root supplies the base (discovered spec) ───────


def test_page_cli_no_out_lands_under_repo_root(tmp_path, monkeypatch, capsys):
    _write_spec(tmp_path, PSPEC)
    pdf = tmp_path / "notes.pdf"
    _prose_pdf(pdf)
    middle = tmp_path / "mid.json"
    _prose_middle(middle)
    _patch_mineru(monkeypatch, middle)
    monkeypatch.chdir(tmp_path)  # discovery walks up from cwd → finds .digest here
    rc = main([str(pdf)])  # no --out; the spec's repo root is the base
    assert rc == 0
    assert (tmp_path / "raw" / "page-0001.png").exists()
    assert (tmp_path / "raw" / "page-0001.md").exists()


def test_out_overrides_spec_base(tmp_path, monkeypatch):
    _write_spec(tmp_path, PSPEC)
    pdf = tmp_path / "notes.pdf"
    _prose_pdf(pdf)
    middle = tmp_path / "mid.json"
    _prose_middle(middle)
    _patch_mineru(monkeypatch, middle)
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "elsewhere"
    rc = main([str(pdf), "--out", str(other)])
    assert rc == 0
    assert (other / "raw" / "page-0001.png").exists()
    assert not (tmp_path / "raw").exists()


def test_unmatched_pdf_falls_back_to_native_layout(tmp_path, monkeypatch):
    _write_spec(tmp_path, PSPEC)  # only matches 'notes'
    pdf = tmp_path / "unrelated.pdf"
    _prose_pdf(pdf)
    middle = tmp_path / "mid.json"
    _prose_middle(middle)
    _patch_mineru(monkeypatch, middle)
    monkeypatch.chdir(tmp_path)
    rc = main([str(pdf)])
    assert rc == 0
    # no rule matched → historical <base>/<stem>/page-NNNN
    assert (tmp_path / "unrelated" / "page-0001.png").exists()
    assert not (tmp_path / "raw").exists()
