"""CLI VLM selection (ADR-0006, stage 5): question strategy must not need the vlm extra."""

from __future__ import annotations

import sys
from argparse import Namespace

import pytest

from ingest_pdf.cli import _make_vlm
from ingest_pdf.vlm.worker import NoVLM, StubVLM


def _ns(**kw) -> Namespace:
    base = dict(stub=False, strategy="auto", model=None, temperature=0.2, repetition_penalty=1.05, max_tokens=4096)
    base.update(kw)
    return Namespace(**base)


def test_stub_returns_stub_vlm():
    assert isinstance(_make_vlm(_ns(stub=True), needs_vlm=True), StubVLM)


def test_no_vlm_when_no_pdf_needs_it_without_loading_mlx():
    vlm = _make_vlm(_ns(strategy="question"), needs_vlm=False)  # must NOT touch mlx-vlm
    assert isinstance(vlm, NoVLM)


def test_when_a_pdf_needs_vlm_without_stub_requires_vlm_extra(monkeypatch):
    # Hermetic: force the mlx-vlm import to fail (None in sys.modules ⇒ ImportError) so this
    # asserts the install-instructions SystemExit regardless of whether the vlm extra happens
    # to be installed in the current venv — the assertion is about the *absent* case.
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    with pytest.raises(SystemExit, match="mlx-vlm"):
        _make_vlm(_ns(strategy="page"), needs_vlm=True)
