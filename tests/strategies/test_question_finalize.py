"""Question finalize: cross-page assembly + cleanup + idempotency (ADR-0006, stage 5)."""

from __future__ import annotations

import json

from PIL import Image

from ingest_pdf.manifest import Manifest
from ingest_pdf.strategies import _crop
from ingest_pdf.strategies import question as qmod

KEY = "/p.pdf"


def _frag_md(page: int, box: list, body: str) -> str:
    return (
        f"<!-- ingest-pdf · mineru@test · 200dpi · strategy question\n"
        f"     source: paper.pdf · page {page} · box {box} -->\n\n"
        f"{body}"
    )


def _unit(name: str, page: int, box: list) -> dict:
    return {"name": name, "image": f"{name}.png", "md": f"{name}.md", "source_page": page, "box": box}


def _setup(tmp_path) -> tuple[Manifest, "object"]:
    out_dir = tmp_path / "paper"
    out_dir.mkdir()
    # page 0: q01 (single), q02 (start);  page 1: q02 (cont), q03 (single)
    frags = {
        "q01__p0001": ((10, 30), (255, 0, 0), 1, [1, 1, 2, 2], "Q1 body"),
        "q02__p0001": ((10, 40), (0, 255, 0), 1, [1, 1, 2, 2], "Q2 part A"),
        "q02__p0002": ((10, 50), (0, 0, 255), 2, [1, 1, 2, 2], "Q2 part B"),
        "q03__p0002": ((10, 20), (255, 255, 0), 2, [1, 1, 2, 2], "Q3 body"),
    }
    for name, (size, color, page, box, body) in frags.items():
        (out_dir / f"{name}.png").write_bytes(_crop.png_bytes(Image.new("RGB", size, color)))
        (out_dir / f"{name}.md").write_text(_frag_md(page, box, body))

    (out_dir / ".renders").mkdir()
    (out_dir / ".renders" / "page-0001.png").write_bytes(b"x")

    m = Manifest(tmp_path / "manifest.json")
    m.set_model("mineru", "test", 200)
    m.ensure_pdf(KEY, "question", {"size": 1, "mtime": 1}, model="mineru@test")
    m.mark_page(
        KEY,
        0,
        "done",
        [_unit("q01__p0001", 1, [1, 1, 2, 2]), _unit("q02__p0001", 1, [1, 1, 2, 2])],
    )
    m.mark_page(
        KEY,
        1,
        "done",
        [_unit("q02__p0002", 2, [1, 1, 2, 2]), _unit("q03__p0002", 2, [1, 1, 2, 2])],
    )
    return m, out_dir


def test_finalize_assembles_and_cleans(tmp_path):
    m, out_dir = _setup(tmp_path)

    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)

    rec = m.data["pdfs"][KEY]
    assert [u["name"] for u in rec["pages"]["0"]["units"]] == ["q01", "q02"]
    assert [u["name"] for u in rec["pages"]["1"]["units"]] == ["q03"]

    assert Image.open(out_dir / "q02.png").size == (10, 90)  # 40 + 50 vertical concat
    assert Image.open(out_dir / "q01.png").size == (10, 30)
    assert Image.open(out_dir / "q03.png").size == (10, 20)

    for frag in ("q01__p0001", "q02__p0001", "q02__p0002", "q03__p0002"):
        assert not (out_dir / f"{frag}.png").exists()
        assert not (out_dir / f"{frag}.md").exists()
    assert not (out_dir / ".renders").exists()

    q02_md = (out_dir / "q02.md").read_text()
    assert "Q2 part A" in q02_md and "Q2 part B" in q02_md
    assert "assembled" in q02_md and "pages 1, 2" in q02_md
    assert "<!-- ingest-pdf" not in q02_md.split("-->", 1)[1]  # no doubled header in the body
    assert "pages 1" in (out_dir / "q01.md").read_text()


def test_finalize_is_idempotent(tmp_path):
    m, out_dir = _setup(tmp_path)
    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)
    q02_png = (out_dir / "q02.png").read_bytes()
    pages_after = json.loads((tmp_path / "manifest.json").read_text())["pdfs"][KEY]["pages"]

    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)  # second run = no-op

    assert (out_dir / "q02.png").read_bytes() == q02_png
    pages_again = json.loads((tmp_path / "manifest.json").read_text())["pdfs"][KEY]["pages"]
    assert [u["name"] for u in pages_again["0"]["units"]] == ["q01", "q02"]
    assert [u["name"] for u in pages_again["1"]["units"]] == ["q03"]
    assert pages_after == pages_again  # manifest stable across re-runs
