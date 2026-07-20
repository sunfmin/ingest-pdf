"""Cropping helpers for the Question strategy (ADR-0006).

Two jobs:
  * snap a question box to the nearest blank horizontal band so an imperfect
    coordinate cuts through whitespace instead of clipping a stem/figure
    (CONTEXT "Region" / ADR-0003);
  * vertically concatenate per-page crops of a cross-page question (PIL — NOT
    pymupdf's Pixmap.copy, which produced white strips on an RGB/alpha mismatch in
    the spike).
"""

from __future__ import annotations

import io

from PIL import Image

Box = tuple[float, float, float, float]
_BLANK_THRESH = 248  # a row is "blank" when every sampled pixel >= this (near-white)
_COL_STRIDE = 3  # sample every Nth column for speed; blank-band detection is coarse


def blank_rows(gray: Image.Image, thresh: int = _BLANK_THRESH) -> list[bool]:
    """Per-row blank flag from an 'L' image (sampled columns for speed)."""
    w, h = gray.size
    px = gray.load()
    cols = range(0, w, _COL_STRIDE)
    out = []
    for y in range(h):
        out.append(all(px[x, y] >= thresh for x in cols))  # sampled cols
    return out


def snap(box: Box, blank: list[bool], search: int = 60) -> tuple[int, int, int, int]:
    """Expand box top/bottom to the nearest blank row within `search` px (clamped).

    If the edge row is already blank it stays put; if no blank row is found within
    the window the original edge is kept (never clip harder than the model's box).
    """
    x0, y0, x1, y1 = box
    h = len(blank)
    yi0 = int(round(y0))
    yi1 = int(round(y1))

    top = yi0
    for y in range(max(0, min(yi0, h - 1)), max(-1, yi0 - search), -1):
        if blank[y]:
            top = y
            break

    bot = yi1
    for y in range(max(0, min(yi1, h - 1)), min(h, yi1 + search)):
        if blank[y]:
            bot = y
            break

    if bot <= top:  # degenerate window — fall back to the un-snapped box
        top, bot = yi0, yi1
    return int(round(x0)), top, int(round(x1)), bot


def crop_box(image: Image.Image, box_px: tuple[int, int, int, int]) -> Image.Image:
    """Crop `image` (RGB) by an integer pixel box, clamped to the image bounds."""
    w, h = image.size
    x0, y0, x1, y1 = box_px
    return image.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1)))


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def concat_vertical(pngs: list[bytes]) -> bytes:
    """Vertically stack PNG crops on a white canvas (cross-page question)."""
    imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in pngs]
    if not imgs:
        return png_bytes(Image.new("RGB", (1, 1), (255, 255, 255)))
    if len(imgs) == 1:
        return png_bytes(imgs[0])
    w = max(i.width for i in imgs)
    h = sum(i.height for i in imgs)
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    y = 0
    for i in imgs:
        canvas.paste(i, (0, y))
        y += i.height
    return png_bytes(canvas)
