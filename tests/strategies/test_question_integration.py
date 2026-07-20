"""End-to-end Question strategy through the pipeline (ADR-0006, stage 4).

Synthetic 2-page PDF + a hand-written middle.json (run_mineru monkeypatched) so no
real MinerU / network. Asserts the zero-VLM bypass, per-page fragment Units, the
per-PDF provenance model, and that the VLM is never transcribed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import fitz
from PIL import Image

from ingest_pdf import pipeline
from ingest_pdf.strategies import _mineru


class _FakeVLM:
    model_id = "stub"
    revision = "m1"

    def __init__(self) -> None:
        self.transcribe = Mock()


def _para(text, bbox):
    return {"bbox": bbox, "type": "text", "lines": [{"spans": [{"type": "text", "content": text}]}]}


def _build_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=400, height=500)
    doc.new_page(width=400, height=500)
    doc.save(path)
    doc.close()


def _fixture_middle(path: Path) -> None:
    data = {
        "pdf_info": [
            {  # page 0
                "para_blocks": [
                    _para("一、选择题：本题共 3 小题", [50, 40, 300, 60]),
                    _para("1. 第一题题干", [50, 80, 350, 100]),
                    _para("2. 第二题题干（跨页起）", [50, 120, 350, 140]),
                ]
            },
            {  # page 1
                "para_blocks": [
                    _para("第二题题干（跨页续）", [50, 40, 350, 60]),
                    _para("3. 第三题题干", [50, 80, 350, 100]),
                ]
            },
        ]
    }
    path.write_text(json.dumps(data), "utf-8")


def test_question_pipeline_zero_vlm_with_cross_page_fragments(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _build_pdf(pdf)
    middle = tmp_path / "fixture_middle.json"
    _fixture_middle(middle)

    monkeypatch.setattr(_mineru, "run_mineru", lambda pdf_, cache, log=print: middle)
    monkeypatch.setattr(_mineru, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    vlm = _FakeVLM()
    counters = pipeline.run([pdf], out_root, "question", vlm, log=lambda *_: None)

    assert counters == {"done": 2, "failed": 0, "skipped": 0}
    vlm.transcribe.assert_not_called()  # zero-VLM bypass worked

    pdf_key = str(pdf.resolve())
    manifest = json.loads((out_root / "manifest.json").read_text())
    rec = manifest["pdfs"][pdf_key]
    assert rec["model"] == "mineru@test"  # per-PDF provenance = strategy model
    assert rec["strategy"] == "question"

    # finalize assembled fragments → one Unit per question; cross-page Q2 lands on its 1st page
    p0_units = [u["name"] for u in rec["pages"]["0"]["units"]]
    p1_units = [u["name"] for u in rec["pages"]["1"]["units"]]
    assert p0_units == ["q01", "q02"]
    assert p1_units == ["q03"]
    assert all(u["box"] is None for u in rec["pages"]["0"]["units"])  # merged units carry no single box

    unit_dir = out_root / "paper"
    for name in ("q01", "q02", "q03"):
        assert (unit_dir / f"{name}.png").exists()
        assert (unit_dir / f"{name}.md").exists()
    # fragments + intermediate full-page renders are cleaned up
    for frag in ("q01__p0001", "q02__p0001", "q02__p0002", "q03__p0002"):
        assert not (unit_dir / f"{frag}.png").exists()
        assert not (unit_dir / f"{frag}.md").exists()
    assert not (unit_dir / ".renders").exists()

    # cross-page Q2 image is the vertical concat of its two fragments ⇒ taller than single-page Qs
    h = {n: Image.open(unit_dir / f"{n}.png").height for n in ("q01", "q02", "q03")}
    assert h["q02"] > h["q01"] and h["q02"] > h["q03"]

    # provenance headers reflect assembly
    q02_md = (unit_dir / "q02.md").read_text()
    assert "mineru@test" in q02_md and "strategy question" in q02_md and "assembled" in q02_md
    assert "pages 1, 2" in q02_md
    assert "pages 1" in (unit_dir / "q01.md").read_text()
    # assembled body still carries the question text from both pages
    assert "第二题题干（跨页起）" in q02_md and "第二题题干（跨页续）" in q02_md
