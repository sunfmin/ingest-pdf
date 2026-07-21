"""Question strategy grouping + emit (ADR-0006). Full + stem variants. No MinerU subprocess."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from ingest_pdf.models import PageJob, PageResult, RenderedPage
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
    stem0 = "".join(b.text for _, b in qs[0].stem)
    sol0 = "".join(b.text for _, b in qs[0].solution)
    assert stem0.startswith("1. 已知集合")
    assert "【答案】A" not in stem0  # the answer is in solution, not stem
    assert sol0 == "【答案】A"  # Q1's solution begins at the 【答案】 block
    assert qs[1].solution == []  # Q2 has no solution marker in this stream
    all_blocks = [b for q in qs for _, b in (q.stem + q.solution)]
    assert all(not b.text.startswith("一、") and not b.text.startswith("二、") for b in all_blocks)


def test_group_records_solution_start_at_first_marker():
    # Q1's solution begins at 【答案】; Q2 has no 【答案】 (MinerU dropped it) so its
    # solution begins at 【分析】 — both must be recognised as the stem cut point.
    stream = [
        (0, _b("一、选择")),
        (0, _b("1. 题干")),  # stem
        (0, _b("选项行")),  # stem
        (0, _b("【答案】C")),  # solution ←
        (0, _b("【解析】…")),  # solution
        (0, _b("2. 题干二")),  # stem
        (0, _b("【分析】直接算")),  # solution ← (no 【答案】 present)
        (0, _b("【详解】…")),  # solution
    ]
    qs = group_questions(stream, log=lambda *_: None)
    assert [b.text for _, b in qs[0].stem] == ["1. 题干", "选项行"]
    assert [b.text for _, b in qs[0].solution] == ["【答案】C", "【解析】…"]
    assert [b.text for _, b in qs[1].stem] == ["2. 题干二"]
    assert [b.text for _, b in qs[1].solution] == ["【分析】直接算", "【详解】…"]


def test_group_two_pass_answer_section():
    # 试卷 restates Q1..Q3 (no solutions), then 参考答案 restates them each with a solution.
    stream = [
        (0, _b("一、选择题")),
        (0, _b("1．（5分）题干一")),
        (0, _b("2．（5分）题干二")),
        (1, _b("3．（5分）题干三")),
        # ── 参考答案与解析 pass ──
        (2, _b("一、选择题")),  # sections repeat — must be skipped, not attached
        (2, _b("1．（5分）题干一")),  # restated statement — dropped
        (2, _b("【分析】解一【解答】A")),
        (2, _b("2．（5分）题干二")),
        (3, _b("【答案】B")),
        (3, _b("【解析】解二")),
        (3, _b("3．（5分）题干三")),
        (3, _b("【分析】解三")),
    ]
    qs = group_questions(stream, log=lambda *_: None)
    assert [q.number for q in qs] == [1, 2, 3]
    # stems come only from the 试卷 pass (pages 0–1), never the restated headers
    assert [(pi, b.text) for pi, b in qs[0].stem] == [(0, "1．（5分）题干一")]
    assert [(pi, b.text) for pi, b in qs[2].stem] == [(1, "3．（5分）题干三")]
    # solutions come only from the 参考答案 pass (pages 2–3); restated statements are gone
    assert [b.text for _, b in qs[0].solution] == ["【分析】解一【解答】A"]
    assert [b.text for _, b in qs[1].solution] == ["【答案】B", "【解析】解二"]
    assert [b.text for _, b in qs[2].solution] == ["【分析】解三"]


def test_group_merged_block_fallback_and_no_period_header():
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
    assert qs[2].stem[0][1].text.startswith("故选 B.")  # merged block became Q3's header
    assert qs[3].stem[-1][1].text == "3. 由上可知 …"  # stray past-number attaches as body


def test_group_no_section_starts_at_first_header():
    stream = [(0, _b("数学")), (0, _b("1. hi")), (0, _b("2. ho"))]
    qs = group_questions(stream, log=lambda *_: None)
    assert [q.number for q in qs] == [1, 2]


def test_build_frags_emits_full_and_stem_per_page():
    qs = group_questions(
        [
            (0, _b("一、选择")),
            (0, _b("1. 题干一", bbox=(50, 80, 350, 100))),
            (0, _b("【答案】A", bbox=(50, 120, 350, 140))),
            (0, _b("2. 题干二起", bbox=(50, 160, 350, 180))),  # cross-page, no answer on p0
            (1, _b("题干二续", bbox=(50, 40, 350, 60))),
            (1, _b("【答案】B", bbox=(50, 80, 350, 100))),
        ],
        log=lambda *_: None,
    )
    frags = _build_frags(qs)
    variants0 = [(f.number, f.variant) for f in frags[0]]
    variants1 = [(f.number, f.variant) for f in frags[1]]
    assert variants0 == [(1, "full"), (1, "stem"), (2, "full"), (2, "stem")]
    assert variants1 == [(2, "full"), (2, "stem")]
    # Q1 stem = header only (the 【答案】 block is excluded); Q2 stem on p1 = cont only (not the answer)
    assert frags[0][1].box_pt == (50, 80, 350, 100)
    assert frags[1][1].box_pt == (50, 40, 350, 60)


def test_build_frags_no_answer_means_no_stem():
    qs = group_questions(
        [(0, _b("一、选择")), (0, _b("1. 题干", bbox=(50, 80, 350, 100))), (0, _b("续", bbox=(50, 120, 350, 140)))],
        log=lambda *_: None,
    )
    frags = _build_frags(qs)
    assert [(f.number, f.variant) for f in frags[0]] == [(1, "full")]


def test_build_frags_stem_when_only_analysis_marker():
    # 【答案】 missing, solution starts at 【分析】 → stem = the header block only.
    qs = group_questions(
        [
            (0, _b("一、选择")),
            (0, _b("1. 题干", bbox=(50, 80, 350, 100))),
            (0, _b("【分析】…", bbox=(50, 120, 350, 140))),
            (0, _b("【详解】…", bbox=(50, 160, 350, 180))),
        ],
        log=lambda *_: None,
    )
    frags = _build_frags(qs)
    assert [(f.number, f.variant) for f in frags[0]] == [(1, "full"), (1, "stem")]
    assert frags[0][1].box_pt == (50, 80, 350, 100)  # stem stops above 【分析】


def test_build_frags_two_pass_full_spans_both_segments():
    # Stem on the 试卷 page (p0), solution on the 参考答案 page (p1). The full variant emits a
    # frag on BOTH pages (finalize stitches them); the stem variant only on the 试卷 page.
    qs = group_questions(
        [
            (0, _b("一、选择题")),
            (0, _b("1．（5分）题干一", bbox=(50, 80, 350, 100))),
            (1, _b("1．（5分）题干一", bbox=(50, 40, 350, 60))),  # restated header — dropped
            (1, _b("【分析】解一", bbox=(50, 80, 350, 120))),
        ],
        log=lambda *_: None,
    )
    frags = _build_frags(qs)
    assert [(f.number, f.variant) for f in frags[0]] == [(1, "full"), (1, "stem")]
    assert [(f.number, f.variant) for f in frags[1]] == [(1, "full")]
    assert frags[0][0].box_pt == (50, 80, 350, 100)  # p0 full = stem only
    assert frags[1][0].box_pt == (50, 80, 350, 120)  # p1 full = solution only (restated header dropped)


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


def _frag(number, page, box_pt, text, variant="full"):
    from ingest_pdf.strategies.question import _Frag

    return _Frag(number=number, page=page, box_pt=box_pt, text=text, variant=variant)


def test_emit_writes_full_and_stem_fragments(tmp_path):
    s = QuestionStrategy()
    s._pages = {
        0: [
            _frag(1, 0, (50, 100, 300, 200), "1. hi full", "full"),
            _frag(1, 0, (50, 100, 300, 150), "1. hi stem", "stem"),
        ]
    }
    rendered = _make_pdf_and_render(tmp_path)  # zoom 2.0

    units = s.emit(rendered, PageResult(markdown="", questions=[]))

    names = sorted(u.name for u in units)
    assert names == ["q01-stem__p0001", "q01__p0001"]
    for u in units:
        img_path = rendered.job.out_dir / u.image_name
        assert img_path.exists() and img_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # white page ⇒ snap is a no-op ⇒ boxes equal the scaled pt boxes
    by_name = {u.name: u for u in units}
    assert by_name["q01__p0001"].box == (100, 200, 600, 400)
    assert by_name["q01-stem__p0001"].box == (100, 200, 600, 300)
    assert by_name["q01-stem__p0001"].md_body == "1. hi stem"
