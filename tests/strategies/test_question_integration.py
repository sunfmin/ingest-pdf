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

    p0_units = [u["name"] for u in rec["pages"]["0"]["units"]]
    p1_units = [u["name"] for u in rec["pages"]["1"]["units"]]
    assert p0_units == ["q01__p0001", "q02__p0001"]
    assert p1_units == ["q02__p0002", "q03__p0002"]

    unit_dir = out_root / "paper"
    for name in p0_units + p1_units:
        assert (unit_dir / f"{name}.png").exists()
        md = (unit_dir / f"{name}.md").read_text()
        assert "mineru@test" in md and "strategy question" in md  # provenance header

    # full-page renders are the crop source (intermediates; cleaned in stage 5)
    assert (unit_dir / ".renders" / "page-0001.png").exists()
    assert (unit_dir / ".renders" / "page-0002.png").exists()
