"""Strategy selection (CONTEXT: auto-detect + override, ADR-0002 / ADR-0006).

A cheap text-layer heuristic — never a VLM or MinerU pass (ADR-0002: "detection is a
cheap heuristic, not another VLM pass"). A scanned PDF has no text layer, so it yields
no signal and falls through to Page; the user overrides with --strategy question there.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from .base import Strategy
from .outline import OutlineStrategy
from .page import PageStrategy
from .question import QuestionStrategy

# 大题头 section header: "一、选择题 …" (the exam sentinel; textbooks use 第一章, no 、).
_SECTION = re.compile(r"^[一二三四五六七八九十]+、")
# A numbered item at line start: "1. …" / "12、…".
_QNUM = re.compile(r"^\d{1,2}[.、]\s")
# A textbook section number: "6.2 …" / "6.2.3 …".
_SECNUM = re.compile(r"^\d+\.\d+")


def _signals(doc: "fitz.Document") -> tuple[int, int, int]:
    n_section = n_qnum = n_secnum = 0
    for page in doc:
        for line in page.get_text().splitlines():
            s = line.strip()
            if not s:
                continue
            if _SECTION.match(s):
                n_section += 1
            if _QNUM.match(s):
                n_qnum += 1
            if _SECNUM.match(s):
                n_secnum += 1
    return n_section, n_qnum, n_secnum


def detect(doc: "fitz.Document", pdf_path: Path) -> Strategy:
    n_section, n_qnum, n_secnum = _signals(doc)
    if n_section >= 1 and n_qnum >= 3:
        return QuestionStrategy()  # exam paper
    if n_secnum >= 5:
        return OutlineStrategy()  # textbook chapter/section density
    return PageStrategy()  # universal fallback (incl. scanned / structure-less)


def get_strategy(name: str, doc: "fitz.Document", pdf_path: Path) -> Strategy:
    if name == "auto":
        return detect(doc, pdf_path)
    if name == "page":
        return PageStrategy()
    if name == "outline":
        return OutlineStrategy()
    if name == "question":
        return QuestionStrategy()
    raise ValueError(f"unknown strategy: {name!r}")
