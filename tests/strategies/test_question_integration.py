"""End-to-end Question strategy through the pipeline (ADR-0006): full + stem Units.

Synthetic 2-page PDF + hand-written middle.json (run_mineru monkeypatched) ⇒ no real
MinerU / network. Asserts the two variants per question, cross-page merge, and per-PDF
provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz
from PIL import Image

from digest_pdf import pipeline
from digest_pdf.strategies import _mineru


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
                    _para("【答案】A", [50, 120, 350, 140]),
                    _para("2. 第二题题干（跨页起）", [50, 160, 350, 180]),
                ]
            },
            {  # page 1
                "para_blocks": [
                    _para("第二题题干（跨页续）", [50, 40, 350, 60]),
                    _para("【答案】B", [50, 80, 350, 100]),
                    _para("3. 第三题题干", [50, 120, 350, 140]),
                    _para("【答案】C", [50, 160, 350, 180]),
                ]
            },
        ]
    }
    path.write_text(json.dumps(data), "utf-8")


def test_question_pipeline_full_and_stem_zero_vlm(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _build_pdf(pdf)
    middle = tmp_path / "fixture_middle.json"
    _fixture_middle(middle)

    monkeypatch.setattr(_mineru, "run_mineru", lambda pdf_, cache, log=print, pages=None: middle)
    monkeypatch.setattr(_mineru, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    counters = pipeline.run([pdf], out_root, "question", log=lambda *_: None)

    assert counters == {"done": 2, "failed": 0, "skipped": 0}

    rec = json.loads((out_root / "manifest.json").read_text())["pdfs"][str(pdf.resolve())]
    assert rec["model"] == "mineru@test" and rec["strategy"] == "question"
    p0 = [u["name"] for u in rec["pages"]["0"]["units"]]
    p1 = [u["name"] for u in rec["pages"]["1"]["units"]]
    assert p0 == ["q01", "q01-stem", "q02", "q02-stem"]  # Q2 (cross-page) lands on its 1st page
    assert p1 == ["q03", "q03-stem"]

    unit_dir = out_root / "paper"
    for name in ("q01", "q01-stem", "q02", "q02-stem", "q03", "q03-stem"):
        assert (unit_dir / f"{name}.png").exists() and (unit_dir / f"{name}.md").exists()
    for frag in (
        "q01__p0001", "q01-stem__p0001", "q02__p0001", "q02-stem__p0001",
        "q02__p0002", "q02-stem__p0002", "q03__p0002", "q03-stem__p0002",
    ):
        assert not (unit_dir / f"{frag}.png").exists()
    assert not (unit_dir / ".renders").exists()

    # stem images exclude the answer line ⇒ shorter than the full image of the same question
    h = {n: Image.open(unit_dir / f"{n}.png").height for n in ("q01", "q01-stem", "q02", "q02-stem")}
    assert h["q01"] > h["q01-stem"]
    assert h["q02"] > h["q02-stem"]

    # stem text has no 【答案】; full text does; cross-page stem still carries the continuation
    q02_stem_md = (unit_dir / "q02-stem.md").read_text()
    assert "【答案】" not in q02_stem_md
    assert "第二题题干（跨页续）" in q02_stem_md
    assert "【答案】B" in (unit_dir / "q02.md").read_text()
    assert "assembled" in q02_stem_md and "pages 1, 2" in q02_stem_md
