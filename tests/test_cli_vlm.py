"""CLI VLM selection (ADR-0006, stage 5): question strategy must not need the vlm extra."""

from __future__ import annotations

from argparse import Namespace

import pytest

from ingest_pdf.cli import _make_vlm
from ingest_pdf.vlm.worker import NoVLM, StubVLM


def _ns(**kw) -> Namespace:
    base = dict(stub=False, strategy="auto", model=None, temperature=0.2, repetition_penalty=1.05, max_tokens=4096)
    base.update(kw)
    return Namespace(**base)


def test_stub_returns_stub_vlm():
    assert isinstance(_make_vlm(_ns(stub=True)), StubVLM)


def test_question_returns_no_vlm_without_loading_mlx():
    vlm = _make_vlm(_ns(strategy="question"))  # must NOT touch mlx-vlm
    assert isinstance(vlm, NoVLM)


def test_non_question_without_stub_requires_vlm_extra():
    # in the test env mlx-vlm is absent ⇒ constructing MlxVLM exits with install instructions
    with pytest.raises(SystemExit, match="mlx-vlm"):
        _make_vlm(_ns(strategy="page"))
