"""Auto-detection heuristic (ADR-0002 / ADR-0006, stage 4). Duck-typed pages (no CJK font)."""

from __future__ import annotations

from pathlib import Path


class _Page:
    def __init__(self, text: str) -> None:
        self._t = text

    def get_text(self, *a, **k) -> str:
        return self._t


def _detect(*texts):
    from digest_pdf.strategies.detect import detect

    return detect([_Page(t) for t in texts], Path("x.pdf")).name


EXAM = "一、选择题：本题共 8 小题\n1. aaa\n2. bbb\n3. ccc\n4. ddd\n二、填空题\n5. eee"
OUTLINE = "1.1 向量\n1.2 数乘\n2.1 坐标\n2.2 模\n3.1 夹角\n3.2 投影"
PROSE = "hello world\nsome prose line\nanother line entirely"


def test_detect_exam():
    assert _detect(EXAM) == "question"


def test_detect_outline():
    assert _detect(OUTLINE) == "outline"


def test_detect_outline_fallback_for_prose():
    # ADR-0010: non-exam docs fall back to Outline (degrades to flat pages in finalize
    # when no section headings are found), no longer to Page.
    assert _detect(PROSE) == "outline"


def test_detect_outline_fallback_for_scanned_empty():
    # scanned (no text layer) → no signal → Outline fallback (ADR-0010)
    assert _detect("") == "outline"


def test_detect_exam_wins_over_outline_density():
    # an exam paper also has section-number-like lines; the 大题头 makes it an exam
    assert _detect(EXAM + "\n1.1 stray\n2.2 stray\n3.3 stray\n4.4 stray\n5.5 stray") == "question"


# ── the Strategy interface is total: every member the pipeline reads is declared ──


def test_strategy_interface_is_total():
    """The pipeline reads name/model_id/revision/finalize as declared members, never by
    getattr/hasattr. Every strategy the factory returns must satisfy that contract."""
    from digest_pdf.strategies.detect import get_strategy

    for name in ("page", "outline", "question"):
        s = get_strategy(name, None, Path("x.pdf"))
        assert s.name == name
        assert isinstance(s.model_id, str) and isinstance(s.revision, str)
        # finalize is a declared member on every strategy — a callable or None, never absent
        assert s.finalize is None or callable(s.finalize)


def test_page_has_no_post_pass_outline_and_question_do():
    """finalize discriminates the post-pass strategies without reflection: Page = None,
    Outline/Question = callable (the pipeline branches on exactly this)."""
    from digest_pdf.strategies.detect import get_strategy

    assert get_strategy("page", None, Path("x.pdf")).finalize is None
    assert callable(get_strategy("outline", None, Path("x.pdf")).finalize)
    assert callable(get_strategy("question", None, Path("x.pdf")).finalize)


# ── resolve_strategy: the one seam --inspect and the pipeline share (no drift) ────


class _FakeMatch:
    def __init__(self, strategy: str) -> None:
        self.rule = type("_Rule", (), {"strategy": strategy})()


class _FakeSpec:
    """Minimal stand-in for LayoutSpec: match(stem) returns a preset Match (or None)."""

    def __init__(self, match) -> None:
        self._match = match

    def match(self, stem: str):
        return self._match


def test_resolve_uses_fallback_when_no_spec():
    from digest_pdf.strategies.detect import resolve_strategy

    res = resolve_strategy(None, Path("x.pdf"), None, "page")
    assert res.strategy.name == "page" and res.match is None


def test_resolve_matched_rule_pins_strategy_over_fallback():
    from digest_pdf.strategies.detect import resolve_strategy

    m = _FakeMatch("question")
    res = resolve_strategy(_FakeSpec(m), Path("真题-2016.pdf"), None, "page")
    # the rule wins over the --strategy fallback; the match rides along for placement/report
    assert res.strategy.name == "question" and res.match is m


def test_resolve_unmatched_falls_back():
    from digest_pdf.strategies.detect import resolve_strategy

    res = resolve_strategy(_FakeSpec(None), Path("x.pdf"), None, "outline")
    assert res.strategy.name == "outline" and res.match is None
