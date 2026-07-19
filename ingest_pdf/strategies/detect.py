"""Strategy selection (CONTEXT: auto-detect + override, ADR-0002).

Milestone 1 wires only the Page Strategy. Auto-detection of Outline vs Question
arrives in milestone 5; Outline/Question strategies in milestones 3/4.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from .base import Strategy
from .outline import OutlineStrategy
from .page import PageStrategy


def detect(doc: "fitz.Document", pdf_path: Path) -> Strategy:
    # TODO(milestone 5): section-number density -> Outline; question markers -> Question.
    return PageStrategy()


def get_strategy(name: str, doc: "fitz.Document", pdf_path: Path) -> Strategy:
    if name == "auto":
        return detect(doc, pdf_path)
    if name == "page":
        return PageStrategy()
    if name == "outline":
        return OutlineStrategy()
    if name == "question":
        raise NotImplementedError("strategy 'question' arrives in milestone 4 (issue #4)")
    raise ValueError(f"unknown strategy: {name!r}")
