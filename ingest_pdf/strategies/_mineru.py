"""MinerU subprocess runner + middle.json parser (ADR-0006, Question strategy).

MinerU is a heavy optional dependency kept OUT of the core venv; we drive it as a
subprocess from an isolated uv venv (built by `ingest install-mineru`) and consume
only its `*_middle.json`. Two lessons from the spike, baked in here:

  * Coordinates come from `pdf_info[pi].para_blocks[].bbox` — these are in **PDF
    point** space. (`*_content_list.json` bboxes are in the layout model's internal
    pixel space and would crop offset — do NOT use them for geometry.)
  * Transcription text is rebuilt from the same para_blocks: text spans verbatim,
    `inline_equation` spans wrapped in `$…$`, `interline_equation` in `$$…$$`. One
    source ⇒ geometry and text can never disagree. The equation-span LaTeX is the
    model's own recognition (cleaner than content_list's, e.g. correct \\sqrt[3]{}).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Isolated venv + modelscope config, both under the user's cache (never the repo).
CACHE_ROOT = Path.home() / ".cache" / "ingest-pdf"
MINERU_VENV = CACHE_ROOT / "mineru-venv"
MINERU_BIN_PATH = MINERU_VENV / "bin" / "mineru"
MINERU_CONFIG_PATH = CACHE_ROOT / "mineru.json"
_MODELSCOPE_CONFIG = {"model-source": "modelscope"}

# Inline vs display formula span types emitted by MinerU's middle.json.
_INLINE_EQ = {"inline_equation"}
_DISPLAY_EQ = {"interline_equation", "display_equation", "isolated_formula"}


@dataclass(frozen=True)
class MBlock:
    """One MinerU paragraph block: PDF-point bbox + reconstructed text + type."""

    bbox: tuple[float, float, float, float]
    text: str
    type: str


def find_mineru_bin() -> list[str] | None:
    """argv prefix to invoke mineru, or None if not installed.

    Order: $MINERU_BIN (override) → the managed venv binary. No silent network
    fallback: if absent we raise with install instructions (mirrors the vlm extra's
    SystemExit idiom in vlm/worker.py).
    """
    env_bin = os.environ.get("MINERU_BIN")
    if env_bin:
        return [env_bin]
    if MINERU_BIN_PATH.exists():
        return [str(MINERU_BIN_PATH)]
    return None


def mineru_pkg_version() -> str:
    """MinerU package version from the managed venv's dist-info (best effort)."""
    site = MINERU_VENV / "lib"
    if not site.exists():
        return "unknown"
    for dist in site.rglob("mineru-*.dist-info/METADATA"):
        try:
            for line in dist.read_text("utf-8").splitlines():
                if line.lower().startswith("version:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            continue
    return "unknown"


def model_identity() -> tuple[str, str]:
    """(model_id, revision) for provenance: ('mineru', <pkg version|unknown>)."""
    return "mineru", mineru_pkg_version()


_NOT_INSTALLED = (
    "MinerU is not installed in the isolated venv at {venv}.\n"
    "The Question strategy needs it for segmentation + transcription (ADR-0006).\n"
    "Install once with:\n"
    "    ingest install-mineru\n"
    "or point $MINERU_BIN at an existing mineru executable."
)


def _write_config() -> Path:
    MINERU_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MINERU_CONFIG_PATH.write_text(json.dumps(_MODELSCOPE_CONFIG), "utf-8")
    return MINERU_CONFIG_PATH


def _find_middle(out_dir: Path) -> Path | None:
    hits = sorted(out_dir.rglob("*_middle.json"))
    return hits[0] if hits else None


def run_mineru(
    pdf: Path,
    cache_dir: Path,
    log: Callable[[str], None] = print,
) -> Path:
    """Run MinerU on `pdf` (idempotent) → path of the produced *_middle.json.

    Skips the subprocess when a middle.json already exists and is at least as new as
    the PDF. Raises SystemExit with install instructions if mineru is unavailable.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir = cache_dir / "mineru_out"
    existing = _find_middle(out_dir)
    if existing and existing.stat().st_mtime >= pdf.stat().st_mtime:
        log(f"  · mineru cache hit: {existing.name}")
        return existing

    bin_argv = find_mineru_bin()
    if bin_argv is None:
        raise SystemExit(_NOT_INSTALLED.format(venv=MINERU_VENV))

    cfg = _write_config()
    env = {**os.environ, "MINERU_TOOLS_CONFIG_JSON": str(cfg)}
    cmd = [*bin_argv, "-p", str(pdf), "-o", str(out_dir), "-b", "hybrid-auto-engine"]
    log(f"  · running mineru on {pdf.name} …")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError("mineru failed:\n" + "\n".join(tail))

    middle = _find_middle(out_dir)
    if middle is None:
        raise RuntimeError(f"mineru produced no *_middle.json under {out_dir}")
    return middle


def _span_text(span: dict) -> str:
    t = span.get("type", "text")
    c = span.get("content", "") or ""
    if t in _INLINE_EQ:
        return f"${c}$"
    if t in _DISPLAY_EQ:
        return f"$${c}$$"
    return c


def _block_text(blk: dict) -> str:
    parts: list[str] = []
    for line in blk.get("lines", []):
        for span in line.get("spans", []):
            parts.append(_span_text(span))
    return "".join(parts)


def parse_blocks(middle: Path) -> dict[int, list[MBlock]]:
    """middle.json → {page_index: [MBlock, ...]} in reading order.

    Geometry from para_blocks bbox (PDF points); text rebuilt per `_span_text`.
    """
    data = json.loads(middle.read_text("utf-8"))
    out: dict[int, list[MBlock]] = {}
    for pi, page in enumerate(data.get("pdf_info", [])):
        blocks: list[MBlock] = []
        for blk in page.get("para_blocks", []):
            bbox = blk.get("bbox")
            if not bbox:
                continue
            blocks.append(
                MBlock(
                    bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                    text=_block_text(blk),
                    type=blk.get("type", "text"),
                )
            )
        out[pi] = blocks
    return out


# ── one-time installer (driven by `ingest install-mineru`) ──────────────────────

_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def install_mineru(log: Callable[[str], None] = print) -> None:
    """Build the isolated mineru venv + download pipeline models via ModelScope.

    Heavy (~2 GB models); run explicitly, never implicitly. Uses a CN mirror for pip
    and ModelScope for models (HF is blocked/unreliable from CN — spike finding).
    """
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("`uv` not found on PATH; install uv first (https://docs.astral.sh/uv/).")

    def run(cmd: list[str]) -> None:
        log("  $ " + " ".join(cmd))
        subprocess.run(cmd, check=True)

    if not MINERU_BIN_PATH.exists():
        log(f"creating isolated venv at {MINERU_VENV} (python 3.12) …")
        run([uv, "venv", str(MINERU_VENV), "--python", "3.12"])
        log("installing mineru[all] (CN mirror) …")
        run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(MINERU_VENV / "bin" / "python"),
                "--index-url",
                _PIP_INDEX,
                "-U",
                "mineru[all]",
            ]
        )
    else:
        log(f"mineru venv already present at {MINERU_VENV}; skipping install.")

    cfg = _write_config()
    env = {**os.environ, "MINERU_TOOLS_CONFIG_JSON": str(cfg)}
    log("downloading pipeline models from ModelScope …")
    subprocess.run(
        [str(MINERU_BIN_PATH.parent / "mineru-models-download"), "-s", "modelscope", "-m", "pipeline"],
        env=env,
        check=True,
    )
    log(f"✓ mineru ready: {' '.join([str(MINERU_BIN_PATH)])} (version {mineru_pkg_version()})")
