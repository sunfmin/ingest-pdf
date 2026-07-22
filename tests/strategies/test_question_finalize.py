"""Question finalize: full + stem cross-page assembly + cleanup + idempotency (ADR-0006)."""

from __future__ import annotations

import json

from PIL import Image

from digest_pdf.manifest import Manifest
from digest_pdf.strategies import _crop
from digest_pdf.strategies import question as qmod

KEY = "/p.pdf"

# name → (size, color, page(1-based), box, body)
_FRAGS = {
    "q01__p0001": ((10, 40), (255, 0, 0), 1, [1, 1, 2, 2], "Q1 full"),
    "q01-stem__p0001": ((10, 20), (200, 0, 0), 1, [1, 1, 2, 2], "Q1 stem"),
    "q02__p0001": ((10, 40), (0, 255, 0), 1, [1, 1, 2, 2], "Q2 part A full"),
    "q02-stem__p0001": ((10, 30), (0, 200, 0), 1, [1, 1, 2, 2], "Q2 part A stem"),
    "q02__p0002": ((10, 50), (0, 128, 0), 2, [1, 1, 2, 2], "Q2 part B full 【答案】 B"),
    "q02-stem__p0002": ((10, 20), (0, 96, 0), 2, [1, 1, 2, 2], "Q2 part B stem"),
    "q03__p0002": ((10, 25), (255, 255, 0), 2, [1, 1, 2, 2], "Q3 full"),
    "q03-stem__p0002": ((10, 15), (200, 200, 0), 2, [1, 1, 2, 2], "Q3 stem"),
}


def _frag_md(page: int, box: list, body: str) -> str:
    return (
        f"<!-- digest-pdf · mineru@test · 200dpi · strategy question\n"
        f"     source: paper.pdf · page {page} · box {box} -->\n\n{body}"
    )


def _unit(name: str, page: int, box: list) -> dict:
    return {"name": name, "image": f"{name}.png", "md": f"{name}.md", "source_page": page, "box": box}


def _setup(tmp_path) -> tuple[Manifest, object]:
    out_dir = tmp_path / "paper"
    out_dir.mkdir()
    for name, (size, color, page, box, body) in _FRAGS.items():
        (out_dir / f"{name}.png").write_bytes(_crop.png_bytes(Image.new("RGB", size, color)))
        (out_dir / f"{name}.md").write_text(_frag_md(page, box, body))
    (out_dir / ".renders").mkdir()
    (out_dir / ".renders" / "page-0001.png").write_bytes(b"x")

    m = Manifest(tmp_path / "manifest.json")
    m.set_model("mineru", "test", 200)
    m.ensure_pdf(KEY, "question", {"size": 1, "mtime": 1}, model="mineru@test")
    m.mark_page(
        KEY, 0, "done",
        [_unit("q01__p0001", 1, [1, 1, 2, 2]), _unit("q01-stem__p0001", 1, [1, 1, 2, 2]),
         _unit("q02__p0001", 1, [1, 1, 2, 2]), _unit("q02-stem__p0001", 1, [1, 1, 2, 2])],
    )
    m.mark_page(
        KEY, 1, "done",
        [_unit("q02__p0002", 2, [1, 1, 2, 2]), _unit("q02-stem__p0002", 2, [1, 1, 2, 2]),
         _unit("q03__p0002", 2, [1, 1, 2, 2]), _unit("q03-stem__p0002", 2, [1, 1, 2, 2])],
    )
    return m, out_dir


def test_finalize_assembles_full_and_stem_and_cleans(tmp_path):
    m, out_dir = _setup(tmp_path)
    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)

    rec = m.data["pdfs"][KEY]
    assert [u["name"] for u in rec["pages"]["0"]["units"]] == ["q01", "q01-stem", "q02", "q02-stem"]
    assert [u["name"] for u in rec["pages"]["1"]["units"]] == ["q03", "q03-stem"]

    assert Image.open(out_dir / "q02.png").size == (10, 90)  # 40 + 50
    assert Image.open(out_dir / "q02-stem.png").size == (10, 50)  # 30 + 20
    assert Image.open(out_dir / "q01.png").size == (10, 40)
    assert Image.open(out_dir / "q01-stem.png").size == (10, 20)

    for frag in _FRAGS:
        assert not (out_dir / f"{frag}.png").exists()
        assert not (out_dir / f"{frag}.md").exists()
    assert not (out_dir / ".renders").exists()

    q02_md = (out_dir / "q02.md").read_text()
    assert "Q2 part A full" in q02_md and "Q2 part B full 【答案】 B" in q02_md
    q02_stem_md = (out_dir / "q02-stem.md").read_text()
    assert "Q2 part A stem" in q02_stem_md and "Q2 part B stem" in q02_stem_md
    assert "【答案】" not in q02_stem_md
    assert "assembled" in q02_stem_md and "pages 1, 2" in q02_stem_md


def test_finalize_is_idempotent(tmp_path):
    m, out_dir = _setup(tmp_path)
    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)
    snap = {n: (out_dir / f"{n}.png").read_bytes() for n in ("q02", "q02-stem")}
    pages_after = json.loads((tmp_path / "manifest.json").read_text())["pdfs"][KEY]["pages"]

    qmod.finalize(out_dir, m, KEY, log=lambda *_: None)  # second run = no-op

    for n, b in snap.items():
        assert (out_dir / f"{n}.png").read_bytes() == b
    pages_again = json.loads((tmp_path / "manifest.json").read_text())["pdfs"][KEY]["pages"]
    assert [u["name"] for u in pages_again["0"]["units"]] == ["q01", "q01-stem", "q02", "q02-stem"]
    assert [u["name"] for u in pages_again["1"]["units"]] == ["q03", "q03-stem"]
    assert pages_after == pages_again
