"""MinerU runner + parser (ADR-0006, stage 2). No network, no real mineru."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

from ingest_pdf.strategies import _mineru as mu


# ── parse_blocks: one source for geometry (para_blocks bbox) + text ─────────────


def _middle(pages):
    return {"pdf_info": [{"para_blocks": b} for b in pages]}


def test_parse_blocks_wraps_formulas_and_keeps_pt_bbox(tmp_path):
    page0 = [
        {  # plain text, two lines
            "bbox": [51, 100, 480, 130],
            "type": "text",
            "lines": [
                {"spans": [{"type": "text", "content": "1. 已知集合 "}]},
                {"spans": [{"type": "text", "content": "，则（）"}]},
            ],
        },
        {  # inline equation span → $…$
            "bbox": [51, 140, 300, 160],
            "type": "text",
            "lines": [
                {
                    "spans": [
                        {"type": "text", "content": "若 "},
                        {"type": "inline_equation", "content": "\\frac{z}{z-1}=1+i"},
                        {"type": "text", "content": "，求 z"},
                    ]
                }
            ],
        },
        {  # display equation span → $$…$$
            "bbox": [120, 200, 400, 240],
            "type": "text",
            "lines": [{"spans": [{"type": "interline_equation", "content": "a^2+b^2=c^2"}]}],
        },
        {"bbox": [], "type": "image", "lines": []},  # empty bbox → skipped
    ]
    mid = tmp_path / "x_middle.json"
    mid.write_text(json.dumps(_middle([page0])), "utf-8")

    out = mu.parse_blocks(mid)
    assert list(out) == [0]
    blocks = out[0]
    assert len(blocks) == 3  # the empty-bbox image block dropped
    assert blocks[0].bbox == (51.0, 100.0, 480.0, 130.0)  # PDF points preserved verbatim
    assert blocks[0].text == "1. 已知集合 ，则（）"
    assert blocks[1].text == "若 $\\frac{z}{z-1}=1+i$，求 z"
    assert blocks[2].text == "$$a^2+b^2=c^2$$"


def test_parse_blocks_multi_page_indices(tmp_path):
    data = _middle([[], [{"bbox": [1, 2, 3, 4], "type": "text", "lines": [{"spans": [{"type": "text", "content": "q"}]}]}]])
    mid = tmp_path / "m_middle.json"
    mid.write_text(json.dumps(data), "utf-8")
    out = mu.parse_blocks(mid)
    assert out[0] == []
    assert len(out[1]) == 1 and out[1][0].text == "q"


# ── find_mineru_bin precedence ──────────────────────────────────────────────────


def test_find_bin_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_BIN", "/custom/mineru")
    monkeypatch.setattr(mu, "MINERU_BIN_PATH", tmp_path / "nope")
    assert mu.find_mineru_bin() == ["/custom/mineru"]


def test_find_bin_managed_venv(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_BIN", raising=False)
    fake = tmp_path / "mineru"
    fake.write_text("")
    monkeypatch.setattr(mu, "MINERU_BIN_PATH", fake)
    assert mu.find_mineru_bin() == [str(fake)]


def test_find_bin_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_BIN", raising=False)
    monkeypatch.setattr(mu, "MINERU_BIN_PATH", tmp_path / "missing")
    assert mu.find_mineru_bin() is None


# ── run_mineru: idempotency + config/env wiring ─────────────────────────────────


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    os.utime(path, (mtime, mtime))


def test_run_mineru_cache_hit_skips_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(mu, "find_mineru_bin", lambda: ["/fake/mineru"])
    run_mock = Mock()
    monkeypatch.setattr("subprocess.run", run_mock)

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fake")
    os.utime(pdf, (1000.0, 1000.0))
    cache = tmp_path / "cache"
    middle = cache / "mineru_out" / "paper" / "hybrid_auto" / "paper_middle.json"
    _touch(middle, 2000.0)  # newer than pdf → hit

    assert mu.run_mineru(pdf, cache, log=lambda *_: None) == middle
    run_mock.assert_not_called()


def test_run_mineru_miss_invokes_with_modelscope_config(monkeypatch, tmp_path):
    monkeypatch.setattr(mu, "find_mineru_bin", lambda: ["/fake/mineru"])

    cache = tmp_path / "cache"
    out_dir = cache / "mineru_out"

    def fake_run(cmd, env=None, **_):
        # emulate mineru writing its middle.json, so run_mineru finds it
        _touch(out_dir / "paper" / "hybrid_auto" / "paper_middle.json", time.time() + 5)
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    run_mock = Mock(side_effect=fake_run)
    monkeypatch.setattr("subprocess.run", run_mock)
    # config writes to the real cache path; redirect it under tmp via module attrs
    monkeypatch.setattr(mu, "MINERU_CONFIG_PATH", tmp_path / "mineru.json")

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fake")
    os.utime(pdf, (5000.0, 5000.0))  # newer than any cached middle → miss

    middle = mu.run_mineru(pdf, cache, log=lambda *_: None)

    run_mock.assert_called_once()
    cmd = run_mock.call_args.args[0]
    assert cmd[:1] == ["/fake/mineru"]
    assert "-b" in cmd and cmd[cmd.index("-b") + 1] == "hybrid-auto-engine"
    assert str(pdf) in cmd
    # modelscope config written + exported to the subprocess env
    cfg = json.loads((tmp_path / "mineru.json").read_text())
    assert cfg == {"model-source": "modelscope"}
    assert run_mock.call_args.kwargs["env"]["MINERU_TOOLS_CONFIG_JSON"] == str(tmp_path / "mineru.json")
    assert middle.name.endswith("_middle.json")


def test_run_mineru_raises_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(mu, "find_mineru_bin", lambda: None)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"x")
    try:
        mu.run_mineru(pdf, tmp_path / "c", log=lambda *_: None)
    except SystemExit as e:
        assert "install-mineru" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit")
