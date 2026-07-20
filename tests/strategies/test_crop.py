"""Cropping helpers (ADR-0006, stage 3)."""

from __future__ import annotations

from PIL import Image

from ingest_pdf.strategies import _crop


def _img_with_bands(w: int, h: int, bands: list[tuple[int, int]]) -> Image.Image:
    im = Image.new("L", (w, h), 255)
    px = im.load()
    for a, b in bands:
        for y in range(a, b + 1):
            for x in range(w):
                px[x, y] = 0
    return im


def test_blank_rows_marks_dark_bands():
    im = _img_with_bands(40, 200, [(40, 60), (120, 140)])
    blank = _crop.blank_rows(im)
    assert blank[10] is True
    assert blank[50] is False
    assert blank[100] is True
    assert blank[130] is False


def test_snap_expands_to_nearest_blank_band():
    im = _img_with_bands(50, 200, [(40, 60)])
    blank = _crop.blank_rows(im)
    x0, y0, x1, y1 = _crop.snap((5, 45, 45, 55), blank, search=60)
    assert (x0, x1) == (5, 45)
    assert y0 < 45 and blank[y0]  # top snapped up onto whitespace
    assert y1 > 55 and blank[y1]  # bottom snapped down onto whitespace
    assert y0 >= 0  # didn't fall off the top


def test_snap_keeps_already_blank_edges():
    im = _img_with_bands(50, 200, [(40, 60)])
    blank = _crop.blank_rows(im)
    _, y0, _, y1 = _crop.snap((5, 30, 45, 70), blank, search=60)
    assert (y0, y1) == (30, 70)


def test_snap_does_not_cross_into_distant_band():
    im = _img_with_bands(50, 300, [(40, 60), (200, 220)])
    blank = _crop.blank_rows(im)
    _, _, _, y1 = _crop.snap((5, 45, 45, 55), blank, search=20)
    assert y1 < 200  # small search window can't reach the second band


def test_crop_box_clamps_to_bounds():
    im = Image.new("RGB", (10, 10), (255, 255, 255))
    cropped = _crop.crop_box(im, (-2, -2, 5, 5))
    assert cropped.size == (5, 5)


def test_concat_vertical_stacks_and_pads():
    a = _crop.png_bytes(Image.new("RGB", (8, 4), (255, 0, 0)))
    b = _crop.png_bytes(Image.new("RGB", (4, 6), (0, 255, 0)))
    out = Image.open(__import__("io").BytesIO(_crop.concat_vertical([a, b])))
    assert out.size == (8, 10)  # width = max, height = sum
