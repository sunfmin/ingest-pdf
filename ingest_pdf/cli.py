"""`ingest` CLI — digest PDFs into a tree of (image, transcription) Units."""

from __future__ import annotations

import argparse
import sys
from typing import Optional


def parse_pages(spec: Optional[str]) -> Optional[set[int]]:
    """'1-4,7' -> {1,2,3,4,7} (1-based). None means all pages."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out or None


def _make_vlm(args):
    """Build the VLM worker per flags. Question strategy ⇒ NoVLM (no vlm extra needed)."""
    if args.stub:
        from .vlm.worker import StubVLM

        return StubVLM()
    if args.strategy == "question":
        from .vlm.worker import NoVLM  # zero-VLM path (ADR-0006)

        return NoVLM()
    from .vlm.worker import DEFAULT_MODEL, MlxVLM

    model_id = args.model or DEFAULT_MODEL
    print(f"loading {model_id} … (once; stays resident)", file=sys.stderr)
    return MlxVLM(
        model_id=model_id,
        temperature=args.temperature,
        repetition_penalty=args.repetition_penalty,
        max_tokens=args.max_tokens,
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ingest",
        description="Digest PDFs into a structured tree of (image, transcription) Units.",
    )
    ap.add_argument("inputs", nargs="*", help="PDF files or directories (recursed for *.pdf)")
    ap.add_argument(
        "--install-mineru",
        action="store_true",
        help="one-time setup: build the isolated MinerU venv + download models (ADR-0006); then exit",
    )
    ap.add_argument("--out", required=True, help="output root directory")
    ap.add_argument(
        "--strategy",
        default="auto",
        choices=["auto", "page", "outline", "question"],
        help="segmentation strategy (default: auto; question=MinerU, zero-VLM, ADR-0006)",
    )
    ap.add_argument("--dpi", type=int, default=200, help="render DPI (default 200)")
    ap.add_argument("--concurrency", type=int, default=None, help="render workers (default: cpu-2)")
    ap.add_argument("--pages", default=None, help="1-based page filter, e.g. '1-4,7' (handy for testing)")
    ap.add_argument("--stub", action="store_true", help="use the milestone-1 stub instead of the real VLM")
    ap.add_argument("--model", default=None, help="mlx VLM model id (default: numind/NuExtract3-mlx-8bits; ADR-0005)")
    ap.add_argument("--temperature", type=float, default=0.2, help="decode temperature (temp=0 degenerates; ADR-0001)")
    ap.add_argument("--repetition-penalty", type=float, default=1.05, help="repetition penalty")
    ap.add_argument("--max-tokens", type=int, default=4096, help="max output tokens per page")
    args = ap.parse_args(argv)

    if args.install_mineru:
        from .strategies._mineru import install_mineru

        install_mineru()
        return 0

    if not args.inputs:
        ap.error("the following arguments are required: inputs (or pass --install-mineru)")

    from .pipeline import run

    vlm = _make_vlm(args)

    try:
        counters = run(
            args.inputs,
            args.out,
            args.strategy,
            vlm,
            dpi=args.dpi,
            n_render=args.concurrency,
            pages=parse_pages(args.pages),
        )
    except NotImplementedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130

    print(
        f"\ndone: {counters['done']}  failed: {counters['failed']}  "
        f"skipped(resume): {counters['skipped']}  →  {args.out}"
    )
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
