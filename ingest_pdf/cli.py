"""`ingest` CLI — digest PDFs into a tree of (image, transcription) Units."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
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


def _needs_vlm(inputs: list[str], spec, arg_strategy: str) -> bool:
    """Would any input PDF actually run a VLM-backed strategy (page/outline)? A Layout
    Spec rule may pin a PDF to the zero-VLM Question path, so an all-question batch loads
    no VLM even under --strategy auto (ADR-0006/0008)."""
    if arg_strategy == "question":
        return False
    import fitz

    from .pipeline import _iter_pdfs
    from .strategies.detect import get_strategy

    for pdf in _iter_pdfs([Path(p) for p in inputs]):
        doc = fitz.open(pdf)
        try:
            m = spec.match(pdf.stem) if spec is not None else None
            if get_strategy(m.rule.strategy if m else arg_strategy, doc, pdf).name in ("page", "outline"):
                return True
        finally:
            doc.close()
    return False


def _make_vlm(args: argparse.Namespace, needs_vlm: bool):
    """Build the VLM worker per flags. No PDF needs a VLM (e.g. all-question) ⇒ NoVLM."""
    if args.stub:
        from .vlm.worker import StubVLM

        return StubVLM()
    if not needs_vlm:
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


def _inspect_estimate(name: str, doc) -> object:
    """Cheap, zero-ML size estimate per resolved strategy (used by `--inspect`)."""
    from .strategies._mineru import MBlock
    from .strategies.question import group_questions

    stream = []
    for pi, page in enumerate(doc):
        for line in page.get_text().splitlines():
            t = line.strip()
            if t:
                stream.append((pi, MBlock(bbox=(0.0, 0.0, 0.0, 0.0), text=t, type="text")))
    if name == "question":
        return len(group_questions(stream, log=lambda *_: None)) if stream else "unknown (scanned)"
    if name == "outline":
        return "chapter/section tree resolved after transcription (ADR-0004)"
    return doc.page_count


def run_inspect(args: argparse.Namespace) -> int:
    """Print a per-PDF structure probe as JSON (no MinerU, no VLM) — the skill's
    'analyze structure + design directory' step, done cheaply by the tool.

    Reports the Layout Spec match per PDF (ADR-0008): the matched rule → resolved
    destination + strategy, or 'unmatched' / 'no-spec'. Report-only — placement is
    not applied here (issue #14). A malformed spec fails fast."""
    import json

    import fitz

    from . import layout
    from .pipeline import _iter_pdfs
    from .strategies.detect import get_strategy

    try:
        spec = layout.load_spec(Path(args.layout) if args.layout else None)
    except layout.LayoutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rows = []
    for pdf in _iter_pdfs([Path(p) for p in args.inputs]):
        doc = fitz.open(pdf)
        try:
            m = spec.match(pdf.stem) if spec else None
            # When a rule matches it pins the strategy; report what would actually run.
            strat = get_strategy(m.rule.strategy if m else args.strategy, doc, pdf)
            name = strat.name
            if spec is None:
                lay = {"status": "no-spec"}
            elif m is None:
                lay = {"status": "unmatched"}
            else:
                lay = {
                    "status": "matched",
                    "rule": m.rule.name,
                    "strategy": m.rule.strategy,
                    "dest": m.resolve(),
                    "captures": {k: v for k, v in m.captures.items() if v is not None},
                }
            rows.append(
                {
                    "path": str(pdf.resolve()),
                    "pages": doc.page_count,
                    "strategy": name,
                    "needs_mineru": name == "question",
                    "needs_vlm": name in ("page", "outline"),
                    "out_subdir": pdf.stem,
                    "estimate": _inspect_estimate(name, doc),
                    "layout": lay,
                }
            )
        finally:
            doc.close()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


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
    ap.add_argument(
        "--inspect",
        action="store_true",
        help="probe each PDF's structure (strategy/pages/estimate) as JSON; no MinerU/VLM; then exit",
    )
    ap.add_argument("--out", default=None, help="output root directory (required unless --inspect/--install-mineru)")
    ap.add_argument(
        "--layout",
        default=None,
        help="path to a Layout Spec (default: auto-discover .ingest/layout.yaml from cwd; ADR-0008)",
    )
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
        ap.error("the following arguments are required: inputs (or pass --inspect / --install-mineru)")

    if args.inspect:
        return run_inspect(args)

    from . import layout

    try:
        spec = layout.load_spec(Path(args.layout) if args.layout else None)
    except layout.LayoutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # The Layout Spec owns the destination: without --out, land under the spec's repo root
    # (the dir containing .ingest/). --out, when given, overrides that base (ADR-0008).
    out_root = args.out or (str(spec.repo_root) if spec else None)
    if not out_root:
        ap.error("--out is required (or add a Layout Spec at .ingest/layout.yaml)")

    from .pipeline import run

    vlm = _make_vlm(args, _needs_vlm(args.inputs, spec, args.strategy))

    try:
        counters = run(
            args.inputs,
            out_root,
            args.strategy,
            vlm,
            dpi=args.dpi,
            n_render=args.concurrency,
            pages=parse_pages(args.pages),
            spec=spec,
        )
    except NotImplementedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130

    print(
        f"\ndone: {counters['done']}  failed: {counters['failed']}  "
        f"skipped(resume): {counters['skipped']}  →  {out_root}"
    )
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
