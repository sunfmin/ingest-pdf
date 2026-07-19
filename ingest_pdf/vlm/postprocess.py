"""Clean raw VLM output into publishable Markdown.

Lifted from the prior impl (transcribe-textbook-page.py), plus wrapper-stripping
for the quirks the smoke test exposed (stray ``` fences and a leading lone `$$`).

Two rules carried over from ADR-0010 of the old repo:
  1. Embedded figures don't enter the text — the VLM fabricates their content
     and emits dangling <img> refs; strip them. The page image is the evidence.
  2. Simple HTML tables → Markdown tables, so their cells' math renders.
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

_FIGURE = re.compile(r"<figure\b.*?</figure>\s*", re.DOTALL | re.IGNORECASE)
_IMG = re.compile(r"<img\b[^>]*?/?>\s*", re.IGNORECASE)
_FIGCAP = re.compile(r"</?figcaption\b[^>]*>", re.IGNORECASE)
# Qwen3-VL emits Markdown images with *fabricated* URLs (e.g. imgur) — strip them too.
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)\s*", re.IGNORECASE)
_BLANKS = re.compile(r"\n{3,}")
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n|\n\s*```\s*$")
_TABLE_BLOCK = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)


def strip_figures(md: str) -> str:
    md = _FIGURE.sub("", md)
    md = _IMG.sub("", md)
    md = _MD_IMG.sub("", md)
    md = _FIGCAP.sub("", md)
    return _BLANKS.sub("\n\n", md).strip()


def strip_wrappers(text: str) -> str:
    """Remove the model's end token, code fences, and a stray leading lone `$$`."""
    text = text.replace("<|im_end|>", "").strip()
    # peel a single wrapping ```lang ... ``` fence if present
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    # drop leading lone `$$` lines the model sometimes prefixes
    lines = text.split("\n")
    while lines and lines[0].strip() == "$$":
        lines.pop(0)
    return "\n".join(lines).strip()


class _TableParser(HTMLParser):
    """Parse one <table> into rows×cells, tracking rowspan/colspan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self.caption = ""
        self._in_caption = False
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {"text": "", "rowspan": int(a.get("rowspan") or "1"), "colspan": int(a.get("colspan") or "1")}
        elif tag == "caption":
            self._in_caption = True
        elif tag == "br":
            if self._cell is not None:
                self._cell["text"] += " "
            elif self._in_caption:
                self.caption += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = self._cell["text"].strip()
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "caption":
            self._in_caption = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"] += data
        elif self._in_caption:
            self.caption += data


def html_table_to_markdown(table_html: str) -> str | None:
    """No-merge <table> → Markdown table; merged cells → None (keep HTML)."""
    p = _TableParser()
    p.feed(table_html)
    rows = [r for r in p.rows if r]
    if not rows:
        return None
    if any(c["rowspan"] > 1 or c["colspan"] > 1 for r in rows for c in r):
        return None
    ncol = max(len(r) for r in rows)

    def fmt(r):
        cells = [c["text"].replace("|", "\\|") for c in r] + [""] * (ncol - len(r))
        return "| " + " | ".join(cells) + " |"

    lines = [fmt(rows[0]), "| " + " | ".join(["---"] * ncol) + " |", *[fmt(r) for r in rows[1:]]]
    md = "\n".join(lines)
    cap = p.caption.strip()
    return f"**{cap}**\n\n{md}" if cap else md


def convert_simple_tables(md: str) -> str:
    return _TABLE_BLOCK.sub(lambda m: html_table_to_markdown(m.group(0)) or m.group(0), md)


def clean(text: str) -> str:
    """Full postprocess pipeline: unwrap → strip figures → convert simple tables."""
    return convert_simple_tables(strip_figures(strip_wrappers(text)))


def model_revision(model_id: str) -> str:
    """The model's HF snapshot commit hash (recorded in provenance). 'unknown' if absent."""
    ref = Path.home() / ".cache/huggingface/hub" / f"models--{model_id.replace('/', '--')}" / "refs/main"
    try:
        return ref.read_text().strip()[:12]
    except OSError:
        return "unknown"
