"""Outline-via-MinerU (ADR-0009): MinerU transcribes each textbook page and the same
outline finalize harvests its `#` section headings into the 第N章/<section>/ tree.

No real MinerU / models: page_markdown is exercised on hand-written middle.json, and the
end-to-end pipeline test monkeypatches run_mineru + model_identity (mirrors the Question
integration test). Asserts the zero-VLM bypass, the section headings survive as Markdown
headings, and the tree (incl. carry-forward + the front bucket) is built.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import fitz

from ingest_pdf import pipeline
from ingest_pdf.strategies import _mineru as mu
from ingest_pdf.strategies import outline


# ── page_markdown: titles → headings, formulas wrapped, images/blanks dropped ────


def _title(text, level=1):
    return {"type": "title", "level": level, "lines": [{"spans": [{"type": "text", "content": text}]}]}


def _text(*spans):
    return {"type": "text", "lines": [{"spans": list(spans)}]}


def _span(content, type="text"):
    return {"type": type, "content": content}


def _middle(pages):
    return {"pdf_info": [{"para_blocks": b} for b in pages]}


def _write_middle(tmp_path, pages) -> Path:
    p = tmp_path / "m_middle.json"
    p.write_text(json.dumps(_middle(pages)), "utf-8")
    return p


def test_title_blocks_become_markdown_headings(tmp_path):
    mid = _write_middle(
        tmp_path,
        [[_title("1.2 集合间的基本关系", level=1), _text(_span("正文一段")), _title("观察", level=2)]],
    )
    md = mu.page_markdown(mid)[0]
    assert md == "# 1.2 集合间的基本关系\n\n正文一段\n\n## 观察"


def test_formula_spans_keep_their_wrap(tmp_path):
    mid = _write_middle(
        tmp_path,
        [[
            _text(_span("若 "), _span("\\frac{z}{z-1}=1+i", "inline_equation"), _span(" 成立")),
            _text(_span("A \\cup B = C", "interline_equation")),
        ]],
    )
    md = mu.page_markdown(mid)[0]
    assert md == "若 $\\frac{z}{z-1}=1+i$ 成立\n\n$$A \\cup B = C$$"


def test_images_and_blank_blocks_are_dropped(tmp_path):
    mid = _write_middle(
        tmp_path,
        [[_title("1.3 集合的基本运算"), {"type": "image", "blocks": [], "lines": []}, _text(_span("  "))]],
    )
    # image + whitespace-only text drop out; only the heading survives
    assert mu.page_markdown(mid)[0] == "# 1.3 集合的基本运算"


def test_level_is_clamped(tmp_path):
    mid = _write_middle(tmp_path, [[_title("深标题", level=9), _title("坏级别", level="x")]])
    assert mu.page_markdown(mid)[0] == "###### 深标题\n\n# 坏级别"


def test_multi_page_indices(tmp_path):
    mid = _write_middle(tmp_path, [[], [_title("1.1 集合的概念")]])
    out = mu.page_markdown(mid)
    assert out[0] == "" and out[1] == "# 1.1 集合的概念"


def test_heading_is_harvestable_by_outline(tmp_path):
    """The whole point: page_markdown's heading feeds outline.section_of_page → tree node."""
    mid = _write_middle(tmp_path, [[_title("1.2 集合间的基本关系"), _text(_span("body"))]])
    assert outline.section_of_page(mu.page_markdown(mid)[0]) == (1, "1.2", "集合间的基本关系")


# ── end-to-end through the pipeline: tree built, zero VLM ─────────────────────────


class _FakeVLM:
    model_id = "stub"
    revision = "m1"

    def __init__(self) -> None:
        self.transcribe = Mock()


def _build_pdf(path: Path, n_pages: int) -> None:
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=400, height=500)
    doc.save(path)
    doc.close()


def _fixture_middle(path: Path) -> None:
    data = _middle(
        [
            [_text(_span("本册前言，无节标题"))],  # p0 → front (no section, nothing to inherit)
            [_title("1.2 集合间的基本关系"), _text(_span("子集与真子集"))],  # p1 → 1.2
            [_text(_span("上一节的延续，仍属 1.2"))],  # p2 → carry-forward 1.2
            [_title("1.3 集合的基本运算"), _text(_span("并集 "), _span("A \\cup B", "inline_equation"))],  # p3 → 1.3
        ]
    )
    path.write_text(json.dumps(data), "utf-8")


def test_outline_mineru_pipeline_builds_tree_zero_vlm(tmp_path, monkeypatch):
    pdf = tmp_path / "textbook.pdf"
    _build_pdf(pdf, 4)
    middle = tmp_path / "fixture_middle.json"
    _fixture_middle(middle)

    monkeypatch.setattr(mu, "run_mineru", lambda pdf_, cache, log=print: middle)
    monkeypatch.setattr(mu, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    vlm = _FakeVLM()
    counters = pipeline.run([pdf], out_root, "outline-mineru", vlm, log=lambda *_: None)

    assert counters == {"done": 4, "failed": 0, "skipped": 0}
    vlm.transcribe.assert_not_called()  # zero-VLM: MinerU owns transcription

    rec = json.loads((out_root / "manifest.json").read_text())["pdfs"][str(pdf.resolve())]
    assert rec["model"] == "mineru@test" and rec["strategy"] == "outline-mineru"

    base = out_root / "textbook"
    # tree: front bucket, section dir, carry-forward onto the heading-less page, next section
    expected = {
        "front/page-0001",
        "第1章/1.2-集合间的基本关系/page-0002",
        "第1章/1.2-集合间的基本关系/page-0003",
        "第1章/1.3-集合的基本运算/page-0004",
    }
    for rel in expected:
        assert (base / f"{rel}.md").exists(), f"missing {rel}.md"
        assert (base / f"{rel}.png").exists(), f"missing {rel}.png"

    # the 1.2 page carries the heading (so the tree could form) and MinerU's body text
    md12 = (base / "第1章/1.2-集合间的基本关系/page-0002.md").read_text("utf-8")
    assert "# 1.2 集合间的基本关系" in md12 and "子集与真子集" in md12
    # inline formula on the 1.3 page kept its $…$ wrap
    md13 = (base / "第1章/1.3-集合的基本运算/page-0004.md").read_text("utf-8")
    assert "$A \\cup B$" in md13
