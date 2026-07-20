"""Question strategy grouping + emit (ADR-0006, stage 3). No MinerU subprocess."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from ingest_pdf.models import PageJob, PageResult, RenderedPage
from ingest_pdf.strategies import _crop
from ingest_pdf.strategies._mineru import MBlock
from ingest_pdf.strategies.question import QuestionStrategy, _build_frags, group_questions


def _b(text: str, bbox=(50, 0, 400, 20)) -> MBlock:
    return MBlock(bbox=bbox, text=text, type="text")


def test_group_gates_preface_and_sections():
    stream = [
        (0, _b("绝密 ★ 启用前")),
        (0, _b("1. 答题前，先将自己的姓名、准考证号填写在试卷上。")),  # preface number — gated
        (0, _b("一、选择题：本题共 8 小题，每小题 5 分。")),  # 大题头 sentinel
        (0, _b("1. 已知集合 $A$，则（）")),
        (0, _b("A. {1}   B. {2}   C. {3}   D. {4}")),
        (0, _b("【答案】A")),
        (0, _b("2. 若 z 满足 …，则 z=（）")),
        (0, _b("二、填空题：本题共 3 小题。")),  # later section header — skipped, not attached
        (0, _b("3. 已知向量 …")),
    ]
    qs = group_questions(stream, log=lambda *_: None)
    assert [q.number for q in qs] == [1, 2, 3]
    texts0 = "".join(b.text for _, b in qs[0].blocks)
    assert texts0.startswith("1. 已知集合")
    assert "【答案】A" in texts0
    assert all(not t.startswith("答题前") for q in qs for _, b in q.blocks for t in [b.text])
    assert all(not b.text.startswith("一、") and not b.text.startswith("二、") for q in qs for _, b in q.blocks)


def test_group_merged_block_fallback_and_no_period_header():
    # Q3's header is merged into Q2's tail block; Q4 has no trailing period ("4 已知").
    stream = [
        (0, _b("一、选择题")),
        (0, _b("1. 第一题")),
        (0, _b("2. 第二题")),
        (0, _b("故选 B.\n3. 第三题（合并块）")),  # merged: expect 3 found after newline
        (0, _b("4 已知 cos 等于 …")),  # optional period
        (0, _b("3. 由上可知 …")),  # stray past number at block start while expecting 5 → body
    ]
    qs = group_questions(stream, log=lambda *_: None)
    assert [q.number for q in qs] == [1, 2, 3, 4]
    assert qs[2].blocks[0][1].text.startswith("故选 B.")  # merged block becomes Q3's first block
    assert qs[3].blocks[-1][1].text == "3. 由上可知 …"  # stray number attached as body, not a new Q


def test_group_no_section_starts_at_first_header():
    stream = [(0, _b("数学")), (0, _b("1. hi")), (0, _b("2. ho"))]
    qs = group_questions(stream, log=lambda *_: None)
    assert [q.number for q in qs] == [1, 2]


def test_build_frags_splits_cross_page_question():
    qs = group_questions(
        [
            (0, _b("一、选择")),
            (0, _b("1. 题干", bbox=(50, 100, 400, 120))),
            (0, _b("续行", bbox=(50, 130, 300, 150))),
            (1, _b("跨页续", bbox=(50, 40, 350, 70))),
        ],
        log=lambda *_: None,
    )
    frags = _build_frags(qs)
    assert sorted(frags) == [0, 1]
    assert len(frags[0]) == 1 and frags[0][0].number == 1
    assert frags[0][0].box_pt == (50, 100, 400, 150)  # union of page-0 blocks
    assert frags[1][0].box_pt == (50, 40, 350, 70)
    assert "跨页续" in frags[1][0].text and "续行" not in frags[1][0].text


# ── emit: scale pt→px, snap, crop, write PNG (synthetic page, no MinerU) ─────────


def _make_pdf_and_render(tmp_path: Path, page_pt=(400, 500), zoom=2.0):
    pdf = tmp_path / "paper.pdf"
    doc = fitz.open()
    doc.new_page(width=page_pt[0], height=page_pt[1])
    doc.save(pdf)
    doc.close()
    out_dir = tmp_path / "out"
    renders = out_dir / ".renders"
    renders.mkdir(parents=True)
    png = renders / "page-0001.png"
    Image.new("RGB", (int(page_pt[0] * zoom), int(page_pt[1] * zoom)), (255, 255, 255)).save(png)
    job = PageJob(pdf_path=pdf, pdf_key=str(pdf), page_index=0, out_dir=out_dir)
    rendered = RenderedPage(job, png, int(page_pt[0] * zoom), int(page_pt[1] * zoom))
    return rendered


def test_emit_crops_one_fragment_per_question(tmp_path):
    s = QuestionStrategy()
    s._pages = {0: [_frag(1, 0, (50, 100, 300, 200), "1. hi")]}
    rendered = _make_pdf_and_render(tmp_path)  # zoom 2.0 → box_px (100,200,600,400)

    units = s.emit(rendered, PageResult(markdown="", questions=[]))

    assert len(units) == 1
    u = units[0]
    assert u.name == "q01__p0001"
    assert u.md_body == "1. hi"
    assert u.source_page == 1
    img_path = rendered.job.out_dir / u.image_name
    assert img_path.exists()
    assert img_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # white page ⇒ every row blank ⇒ snap is a no-op ⇒ box == scaled pt box
    assert u.box == (100, 200, 600, 400)
    # and the crop dimensions match that box
    assert Image.open(img_path).size == (500, 200)


def _frag(number, page, box_pt, text):
    from ingest_pdf.strategies.question import _Frag

    return _Frag(number=number, page=page, box_pt=box_pt, text=text)
