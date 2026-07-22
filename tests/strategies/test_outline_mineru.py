"""Outline via MinerU (ADR-0009/0010): MinerU transcribes each textbook page (Outline now
subclasses the MinerU-backed PageStrategy) and the outline finalize harvests its `#` section
headings into the 第N章/<section>/ tree — or leaves the pages flat when a doc has no sections.

No real MinerU / models: page_markdown is exercised on hand-written middle.json, and the
end-to-end pipeline tests monkeypatch run_mineru + model_identity (mirrors the Question
integration test). Asserts the section headings survive as Markdown headings, the tree
(incl. carry-forward + the front bucket) is built, and the graceful degrade to flat pages
when no section heading is present.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz

from digest_pdf import pipeline
from digest_pdf.strategies import _mineru as mu
from digest_pdf.strategies import outline


# ── page_markdown: titles → headings, formulas wrapped, images/blanks dropped ────


def _title(text, level=1):
    return {"type": "title", "level": level, "lines": [{"spans": [{"type": "text", "content": text}]}]}


def _text(*spans):
    return {"type": "text", "lines": [{"spans": list(spans)}]}


def _span(content, type="text"):
    return {"type": type, "content": content}


def _image(path, caption=""):
    blocks = [{"type": "image_body", "lines": [{"spans": [{"type": "image", "image_path": path}]}]}]
    if caption:
        blocks.append({"type": "image_caption", "lines": [{"spans": [{"type": "text", "content": caption}]}]})
    return {"type": "image", "blocks": blocks, "lines": []}


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


def test_image_block_becomes_figure_ref(tmp_path):
    mid = _write_middle(tmp_path, [[_title("1.3 x"), _image("abc123.jpg", "图1.3-1"), _text(_span("body"))]])
    assert mu.page_markdown(mid)[0] == "# 1.3 x\n\n![图1.3-1](page-0001.fig-1.jpg)\n\nbody"
    assert mu.page_figures(mid) == {0: [("page-0001.fig-1.jpg", "abc123.jpg")]}


# ── end-to-end through the pipeline: tree built (MinerU sole transcriber) ─────────


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

    monkeypatch.setattr(mu, "run_mineru", lambda pdf_, cache, log=print, pages=None: middle)
    monkeypatch.setattr(mu, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    counters = pipeline.run([pdf], out_root, "outline", log=lambda *_: None)

    assert counters == {"done": 4, "failed": 0, "skipped": 0}

    rec = json.loads((out_root / "manifest.json").read_text())["pdfs"][str(pdf.resolve())]
    assert rec["model"] == "mineru@test" and rec["strategy"] == "outline"

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


def test_outline_no_sections_degrades_to_flat(tmp_path, monkeypatch):
    """A non-textbook auto-routed to outline (no N.N headings) stays flat — no tree, no
    front/ bucket (ADR-0010 graceful degrade)."""
    pdf = tmp_path / "prose.pdf"
    _build_pdf(pdf, 2)
    middle = _write_middle(tmp_path, [[_text(_span("just prose"))], [_text(_span("more prose"))]])

    monkeypatch.setattr(mu, "run_mineru", lambda p, cache, log=print, pages=None: middle)
    monkeypatch.setattr(mu, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    counters = pipeline.run([pdf], out_root, "outline", log=lambda *_: None)
    assert counters == {"done": 2, "failed": 0, "skipped": 0}

    base = out_root / "prose"
    assert (base / "page-0001.md").exists() and (base / "page-0002.md").exists()  # flat
    assert not (base / "front").exists()          # no front bucket
    assert not list(base.glob("第*章"))            # no chapter tree


def test_figures_are_copied_and_move_with_the_page(tmp_path, monkeypatch):
    """A page's inlined figure is copied out under a page-scoped name and moved into the
    section dir alongside its page by finalize (ADR-0010 figure inlining)."""
    pdf = tmp_path / "book.pdf"
    _build_pdf(pdf, 2)
    md_dir = tmp_path / "mineru"
    (md_dir / "images").mkdir(parents=True)
    (md_dir / "images" / "venn.jpg").write_bytes(b"\xff\xd8fake-jpg-bytes")
    middle = md_dir / "book_middle.json"
    middle.write_text(
        json.dumps(
            _middle(
                [
                    [_text(_span("intro, no section"))],  # p0 → front
                    [_title("1.3 集合的基本运算"), _image("venn.jpg", "图1.3-1"), _text(_span("并集"))],  # p1 → 1.3
                ]
            )
        ),
        "utf-8",
    )
    monkeypatch.setattr(mu, "run_mineru", lambda p, cache, log=print, pages=None: middle)
    monkeypatch.setattr(mu, "model_identity", lambda: ("mineru", "test"))

    out_root = tmp_path / "out"
    counters = pipeline.run([pdf], out_root, "outline", log=lambda *_: None)
    assert counters == {"done": 2, "failed": 0, "skipped": 0}

    secdir = out_root / "book" / "第1章" / "1.3-集合的基本运算"
    fig = secdir / "page-0002.fig-1.jpg"
    assert fig.exists() and fig.read_bytes() == b"\xff\xd8fake-jpg-bytes"  # copied + moved with page
    assert "![图1.3-1](page-0002.fig-1.jpg)" in (secdir / "page-0002.md").read_text("utf-8")
