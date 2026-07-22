"""`digest --inspect` structure probe (skill's analyze step): zero MinerU / zero VLM."""

from __future__ import annotations

import json

import fitz
import pytest

from digest_pdf.cli import main


def _mk(path, lines):
    doc = fitz.open()
    p = doc.new_page()
    y = 50
    for ln in lines:
        p.insert_text((50, y), ln)
        y += 20
    doc.save(path)
    doc.close()


def _inspect(capsys, *argv) -> list:
    rc = main(["--inspect", *argv])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_inspect_question_counts_text_layer_questions(tmp_path, capsys):
    pdf = tmp_path / "exam.pdf"
    _mk(pdf, ["1. aaa", "2. bbb", "3. ccc"])
    rows = _inspect(capsys, str(pdf), "--strategy", "question")
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy"] == "question"
    assert r["estimate"] == 3
    assert r["needs_mineru"] is True and r["needs_vlm"] is False
    assert r["pages"] == 1 and r["out_subdir"] == "exam"


def test_inspect_auto_prose_falls_back_to_outline(tmp_path, capsys):
    pdf = tmp_path / "notes.pdf"
    _mk(pdf, ["hello world", "foo bar"])
    r = _inspect(capsys, str(pdf))[0]  # default auto → Outline fallback (ADR-0010)
    assert r["strategy"] == "outline"
    assert isinstance(r["estimate"], str) and "ADR-0004" in r["estimate"]
    assert r["needs_mineru"] is True and r["needs_vlm"] is False


def test_inspect_question_on_scanned_is_unknown(tmp_path, capsys):
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page()  # no text layer
    doc.save(pdf)
    doc.close()
    r = _inspect(capsys, str(pdf), "--strategy", "question")[0]
    assert r["estimate"] == "unknown (scanned)"
    assert r["needs_mineru"] is True


def test_inspect_outline_estimate_is_deferred_string(tmp_path, capsys):
    pdf = tmp_path / "book.pdf"
    _mk(pdf, ["some prose"])
    r = _inspect(capsys, str(pdf), "--strategy", "outline")[0]
    assert r["strategy"] == "outline"
    assert isinstance(r["estimate"], str) and "ADR-0004" in r["estimate"]
    assert r["needs_mineru"] is True and r["needs_vlm"] is False


def test_inspect_batch_returns_one_row_per_pdf(tmp_path, capsys):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _mk(a, ["1. x"])
    _mk(b, ["prose"])
    rows = _inspect(capsys, str(a), str(b), "--strategy", "question")
    assert [r["out_subdir"] for r in rows] == ["a", "b"]


def test_inspect_does_not_require_out(tmp_path, capsys):
    pdf = tmp_path / "z.pdf"
    _mk(pdf, ["1. x"])
    # no --out given; --inspect must not error
    assert main(["--inspect", str(pdf), "--strategy", "question"]) == 0


def test_run_without_out_errors(tmp_path):
    pdf = tmp_path / "z.pdf"
    _mk(pdf, ["1. x"])
    with pytest.raises(SystemExit):
        main([str(pdf)])  # neither --out nor --inspect/--install-mineru
