"""Auto-detection heuristic (ADR-0002 / ADR-0006, stage 4). Duck-typed pages (no CJK font)."""

from __future__ import annotations

from pathlib import Path


class _Page:
    def __init__(self, text: str) -> None:
        self._t = text

    def get_text(self, *a, **k) -> str:
        return self._t


def _detect(*texts):
    from ingest_pdf.strategies.detect import detect

    return detect([_Page(t) for t in texts], Path("x.pdf")).name


EXAM = "一、选择题：本题共 8 小题\n1. aaa\n2. bbb\n3. ccc\n4. ddd\n二、填空题\n5. eee"
OUTLINE = "1.1 向量\n1.2 数乘\n2.1 坐标\n2.2 模\n3.1 夹角\n3.2 投影"
PROSE = "hello world\nsome prose line\nanother line entirely"


def test_detect_exam():
    assert _detect(EXAM) == "question"


def test_detect_outline():
    assert _detect(OUTLINE) == "outline"


def test_detect_page_for_prose():
    assert _detect(PROSE) == "page"


def test_detect_page_for_scanned_empty():
    assert _detect("") == "page"


def test_detect_exam_wins_over_outline_density():
    # an exam paper also has section-number-like lines; the 大题头 makes it an exam
    assert _detect(EXAM + "\n1.1 stray\n2.2 stray\n3.3 stray\n4.4 stray\n5.5 stray") == "question"
