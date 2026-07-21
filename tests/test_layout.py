"""Layout Spec (ADR-0008): discovery/override, first-match-wins, validation,
capture resolution, and --inspect reporting. Pure parse/validate/match — no placement."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from ingest_pdf import layout
from ingest_pdf.cli import main

GOOD_SPEC = r"""
rules:
  - name: 浙江高考数学真题
    match: '(?P<year>\d{4})年(?P<region>浙江)高考数学(?:【(?P<subject>[理文])】)?'
    strategy: question
    path: '真题/{region}/{year}/{subject}/q{qno}'
  - name: 数学教材
    match: '(?P<subject>数学)教材'
    strategy: outline
    path: '教材/{subject}/{section}'
"""


def _write_spec(root: Path, text: str = GOOD_SPEC) -> Path:
    d = root / ".ingest"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "layout.yaml"
    p.write_text(text, "utf-8")
    return p


# ── discovery / override ─────────────────────────────────────────────────────


def test_absent_spec_returns_none(tmp_path):
    assert layout.load_spec(start=tmp_path) is None


def test_discovery_walks_up_from_cwd(tmp_path):
    _write_spec(tmp_path)
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    found = layout.discover_spec_path(start=deep)
    assert found == tmp_path / ".ingest" / "layout.yaml"


def test_explicit_layout_override_loads_and_sets_repo_root(tmp_path):
    p = _write_spec(tmp_path)
    spec = layout.load_spec(explicit=p)
    assert spec is not None
    assert spec.repo_root == tmp_path  # dir containing .ingest/
    assert [r.name for r in spec.rules] == ["浙江高考数学真题", "数学教材"]


def test_explicit_missing_file_errors(tmp_path):
    with pytest.raises(layout.LayoutError):
        layout.load_spec(explicit=tmp_path / "nope.yaml")


# ── matching / resolution ────────────────────────────────────────────────────


def test_first_match_wins(tmp_path):
    spec = layout.load_spec(explicit=_write_spec(tmp_path))
    m = spec.match("2016年浙江高考数学【理】（解析版）")
    assert m is not None and m.rule.name == "浙江高考数学真题"
    assert m.rule.strategy == "question"
    assert m.captures["year"] == "2016" and m.captures["region"] == "浙江" and m.captures["subject"] == "理"


def test_resolve_fills_captures_keeps_structural(tmp_path):
    spec = layout.load_spec(explicit=_write_spec(tmp_path))
    m = spec.match("2016年浙江高考数学【理】（解析版）")
    assert m.resolve() == "真题/浙江/2016/理/q{qno}"


def test_resolve_drops_absent_optional_segment(tmp_path):
    spec = layout.load_spec(explicit=_write_spec(tmp_path))
    m = spec.match("2018年浙江高考数学（解析版）")  # no 【理/文】
    assert m.captures.get("subject") is None
    assert m.resolve() == "真题/浙江/2018/q{qno}"


def test_no_rule_matches_returns_none(tmp_path):
    spec = layout.load_spec(explicit=_write_spec(tmp_path))
    assert spec.match("完全不相关的文件名") is None


# ── validation (each fails fast with LayoutError) ────────────────────────────


@pytest.mark.parametrize(
    "spec_text",
    [
        # bad regex
        "rules:\n  - {name: r, match: '(?P<y>[', strategy: page, path: 'p/page-{page}'}",
        # unknown strategy
        "rules:\n  - {name: r, match: 'x', strategy: nope, path: 'p/{page}'}",
        # missing required key ('path')
        "rules:\n  - {name: r, match: 'x', strategy: page}",
        # unknown token (not a capture, not structural)
        "rules:\n  - {name: r, match: 'x', strategy: page, path: 'p/{bogus}/page-{page}'}",
        # structural token belonging to another strategy
        "rules:\n  - {name: r, match: 'x', strategy: page, path: 'p/q{qno}'}",
        # missing the strategy's structural token entirely
        "rules:\n  - {name: r, match: '(?P<y>x)', strategy: page, path: 'p/{y}'}",
        # structural token present but not in the terminal segment
        "rules:\n  - {name: r, match: '(?P<y>x)', strategy: page, path: 'p/page-{page}/{y}'}",
        # top-level not a mapping with rules
        "- just\n- a\n- list",
        # rules not a non-empty list
        "rules: []",
    ],
)
def test_validation_rejects(tmp_path, spec_text):
    with pytest.raises(layout.LayoutError):
        layout.parse_spec(spec_text, tmp_path / ".ingest" / "layout.yaml")


def test_valid_page_and_outline_specs_parse(tmp_path):
    text = (
        "rules:\n"
        "  - {name: pg, match: 'notes', strategy: page, path: 'raw/{page}'}\n"
        "  - {name: bk, match: 'book', strategy: outline, path: 'chapters/{section}'}\n"
    )
    spec = layout.parse_spec(text, tmp_path / "layout.yaml")
    assert [r.strategy for r in spec.rules] == ["page", "outline"]


# ── --inspect reporting ──────────────────────────────────────────────────────


def _mk_question_pdf(path: Path):
    doc = fitz.open()
    p = doc.new_page()
    y = 50
    for ln in ["一、选择题", "1. aaa", "2. bbb", "3. ccc"]:
        p.insert_text((50, y), ln)
        y += 20
    doc.save(path)
    doc.close()


def _inspect(capsys, *argv) -> list:
    rc = main(["--inspect", *argv])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_inspect_reports_matched_rule(tmp_path, capsys):
    _write_spec(tmp_path)
    pdf = tmp_path / "2016年浙江高考数学【理】（解析版）.pdf"
    _mk_question_pdf(pdf)
    r = _inspect(capsys, str(pdf), "--layout", str(tmp_path / ".ingest" / "layout.yaml"))[0]
    assert r["strategy"] == "question"  # rule pins it, over auto
    assert r["layout"] == {
        "status": "matched",
        "rule": "浙江高考数学真题",
        "strategy": "question",
        "dest": "真题/浙江/2016/理/q{qno}",
        "captures": {"year": "2016", "region": "浙江", "subject": "理"},
    }


def test_inspect_reports_unmatched(tmp_path, capsys):
    _write_spec(tmp_path)
    pdf = tmp_path / "unrelated.pdf"
    _mk_question_pdf(pdf)
    r = _inspect(capsys, str(pdf), "--layout", str(tmp_path / ".ingest" / "layout.yaml"))[0]
    assert r["layout"] == {"status": "unmatched"}


def test_inspect_reports_no_spec(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .ingest/ up the tree
    pdf = tmp_path / "exam.pdf"
    _mk_question_pdf(pdf)
    r = _inspect(capsys, str(pdf), "--strategy", "question")[0]
    assert r["layout"] == {"status": "no-spec"}


def test_inspect_malformed_spec_fails_fast(tmp_path, capsys):
    _write_spec(tmp_path, "rules: []")
    pdf = tmp_path / "exam.pdf"
    _mk_question_pdf(pdf)
    rc = main(["--inspect", str(pdf), "--layout", str(tmp_path / ".ingest" / "layout.yaml")])
    assert rc == 2
