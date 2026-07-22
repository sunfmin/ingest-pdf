"""Page → image via pymupdf get_pixmap (lifted from the prior impl's renderer).

fitz.Document is not safe to share across threads, so each render thread keeps
its own per-PDF Document in thread-local storage.
"""

from __future__ import annotations

import threading
from pathlib import Path

import fitz  # pymupdf

_tls = threading.local()


def _doc(pdf_path: Path) -> fitz.Document:
    cache = getattr(_tls, "docs", None)
    if cache is None:
        cache = _tls.docs = {}
    key = str(pdf_path)
    doc = cache.get(key)
    if doc is None:
        doc = cache[key] = fitz.open(pdf_path)
    return doc


def render_page(pdf_path: Path, page_index: int, dpi: int, out_png: Path) -> tuple[int, int]:
    """Render one page to a PNG at `dpi`. Returns (width, height)."""
    doc = _doc(pdf_path)
    zoom = dpi / 72.0
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out_png)
    return pix.width, pix.height
