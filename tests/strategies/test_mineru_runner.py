"""MinerU runner + parser (ADR-0006, stage 2). No network, no real mineru."""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import httpx

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


# ── warm-server (MINERU_API_URL) HTTP path ───────────────────────────────────────


def _api_zip(stem: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{stem}/hybrid_auto/{stem}_middle.json", json.dumps({"pdf_info": [{"para_blocks": []}]}))
    return buf.getvalue()


class _FakeResp:
    status_code = 200

    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


def test_run_mineru_uses_warm_api_when_url_set(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:9")
    calls = []

    def fake_post(url, *, data=None, files=None, timeout=None, follow_redirects=False, **_):
        calls.append((url, data, files))
        return _FakeResp(_api_zip("paper"))

    monkeypatch.setattr(httpx, "post", fake_post)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    middle = mu.run_mineru(pdf, tmp_path / "c", log=lambda *_: None)

    assert len(calls) == 1
    url, data, files = calls[0]
    assert url.endswith("/file_parse")
    assert data["backend"] == "hybrid-engine" and data["parse_method"] == "auto"
    assert data["return_middle_json"] == "true" and data["response_format_zip"] == "true"
    assert files[0][0] == "files"
    assert middle.exists() and middle.name == "paper_middle.json"
    # zip unpacked into the canonical CLI layout under out_dir
    assert (tmp_path / "c" / "mineru_out" / "paper" / "hybrid_auto" / "paper_middle.json").exists()


def test_run_mineru_cache_hit_skips_api(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:9")
    post = Mock()
    monkeypatch.setattr(httpx, "post", post)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"x")
    os.utime(pdf, (1000.0, 1000.0))
    cache = tmp_path / "c"
    middle = cache / "mineru_out" / "p" / "hybrid_auto" / "p_middle.json"
    middle.parent.mkdir(parents=True)
    middle.write_text("{}")
    os.utime(middle, (2000.0, 2000.0))

    assert mu.run_mineru(pdf, cache, log=lambda *_: None) == middle
    post.assert_not_called()  # cache hit short-circuits before the network


def test_run_mineru_api_failure_falls_back_to_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:9")

    def _boom(*_a, **_k):
        raise httpx.ConnectError("down", request=httpx.Request("POST", "http://127.0.0.1:9/file_parse"))

    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(mu, "find_mineru_bin", lambda: ["/fake/mineru"])
    monkeypatch.setattr(mu, "MINERU_CONFIG_PATH", tmp_path / "mineru.json")

    cache = tmp_path / "c"
    out_dir = cache / "mineru_out"

    def fake_run(cmd, env=None, **_):
        (out_dir / "paper" / "hybrid_auto").mkdir(parents=True, exist_ok=True)
        (out_dir / "paper" / "hybrid_auto" / "paper_middle.json").write_text("{}")
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    run_mock = Mock(side_effect=fake_run)
    monkeypatch.setattr("subprocess.run", run_mock)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    os.utime(pdf, (5000.0, 5000.0))

    middle = mu.run_mineru(pdf, cache, log=lambda *_: None)
    run_mock.assert_called_once()  # API connect failed → CLI ran
    assert middle.exists()
