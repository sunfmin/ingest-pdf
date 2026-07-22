"""MinerU subprocess runner + middle.json parser (ADR-0006, Question strategy).

MinerU is a heavy optional dependency kept OUT of the core venv; we drive it as a
subprocess from an isolated uv venv (built by `digest install-mineru`) and consume
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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Isolated venv + modelscope config, both under the user's cache (never the repo).
CACHE_ROOT = Path.home() / ".cache" / "digest-pdf"
MINERU_VENV = CACHE_ROOT / "mineru-venv"
MINERU_BIN_PATH = MINERU_VENV / "bin" / "mineru"
MINERU_API_BIN_PATH = MINERU_VENV / "bin" / "mineru-api"  # warm HTTP server (`digest --serve-mineru`)
MINERU_CONFIG_PATH = CACHE_ROOT / "mineru.json"
_MODELSCOPE_CONFIG = {"model-source": "modelscope"}

# Backend passed to `mineru -b`. `hybrid-auto-engine` blends the pipeline models with
# the MinerU2.5 VLM and, on Apple Silicon (macOS 13.5+), auto-selects MLX for the VLM
# half — so the exam path already runs the bundled MinerU2.5-Pro VLM under MLX (ADR-0007).
# Single source of truth: used in run_mineru's argv and folded into provenance revision.
MINERU_BACKEND = "hybrid-auto-engine"

# Model caches MinerU downloads into. The VLM model version is bundled with the mineru
# package (no per-run flag selects it); we read its on-disk name for provenance.
_MODEL_CACHE_DIRS = (
    Path.home() / ".cache" / "modelscope" / "models",
    Path.home() / ".cache" / "huggingface" / "hub",
)

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
    fallback: if absent the caller raises with install instructions (see run_mineru).
    """
    env_bin = os.environ.get("MINERU_BIN")
    if env_bin:
        return [env_bin]
    if MINERU_BIN_PATH.exists():
        return [str(MINERU_BIN_PATH)]
    return None


def mineru_installed() -> bool:
    """True when MinerU is available — the managed venv binary exists, or $MINERU_BIN
    points at an external install. Cheap: no subprocess, no network. Backs the
    `digest --mineru-status` readiness gate so callers never stat a cache path."""
    return bool(os.environ.get("MINERU_BIN")) or MINERU_BIN_PATH.exists()


def serve_mineru(port: int = 8765, log: Callable[[str], None] = print) -> int:
    """Exec the managed ``mineru-api`` warm server on 127.0.0.1:``port``.

    A warm server keeps the models resident across a whole batch (the per-PDF cold
    load is the slow part); the pipeline's runner talks to it via ``MINERU_API_URL``.
    The tool owns the venv + modelscope-config paths, so an orchestrator only needs to
    background this and poll ``/health`` — it never hardcodes a cache directory.

    Returns 2 with install guidance if MinerU isn't installed. On success it does not
    return: the process image is replaced by ``mineru-api``, so a backgrounded caller's
    PID *is* the server and a plain ``kill`` tears it down cleanly."""
    if not MINERU_API_BIN_PATH.exists():
        print("MinerU is not installed — run `digest --install-mineru` first.", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["MINERU_TOOLS_CONFIG_JSON"] = str(MINERU_CONFIG_PATH)
    log(f"serving mineru-api at http://127.0.0.1:{port}  (export MINERU_API_URL to this)")
    sys.stdout.flush()
    os.execve(
        str(MINERU_API_BIN_PATH),
        [str(MINERU_API_BIN_PATH), "--host", "127.0.0.1", "--port", str(port)],
        env,
    )


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


def _detected_vlm_model() -> str | None:
    """Best-effort name of the MinerU2.5 VLM model on disk, e.g. 'MinerU2.5-Pro-2605-1.2B'.

    Cache dir names are org-prefixed — 'OpenDataLab--MinerU2.5-Pro-2605-1.2B' (modelscope)
    or 'models--OpenDataLab--MinerU2.5-Pro-2605-1.2B' (HF hub) — so we take the segment
    after the last '--'. If several versions are cached we pick the lexically-latest
    (…-Pro-2605 > …-Pro-2604 > …-2509), which matches the newest install in practice.
    """
    names: set[str] = set()
    for root in _MODEL_CACHE_DIRS:
        if not root.exists():
            continue
        for p in root.iterdir():
            if p.is_dir() and "MinerU2.5" in p.name:
                names.add(p.name.split("--")[-1])
    return max(names) if names else None


def model_identity() -> tuple[str, str]:
    """(model_id, revision) for provenance.

    model_id is the actual recognition model — the MinerU2.5 VLM name detected on disk —
    so a Unit's header names exactly what transcribed it (e.g. 'MinerU2.5-Pro-2605-1.2B').
    revision pins the mineru package version + backend ('mineru3.4.4-hybrid'); a model
    upgrade (a new bundled VLM ships with a new package version) or a backend switch both
    change it and so force re-Calibration. Falls back to the generic ('mineru', <pkg
    version>) when the model dir can't be located (ADR-0007).
    """
    ver = mineru_pkg_version()
    model = _detected_vlm_model()
    if model is None:
        return "mineru", ver
    backend = MINERU_BACKEND.removesuffix("-auto-engine").removesuffix("-engine")
    return model, f"mineru{ver}-{backend}"


_NOT_INSTALLED = (
    "MinerU is not installed in the isolated venv at {venv}.\n"
    "The Question strategy needs it for segmentation + transcription (ADR-0006).\n"
    "Install once with:\n"
    "    digest install-mineru\n"
    "or point $MINERU_BIN at an existing mineru executable."
)


def _write_config() -> Path:
    MINERU_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MINERU_CONFIG_PATH.write_text(json.dumps(_MODELSCOPE_CONFIG), "utf-8")
    return MINERU_CONFIG_PATH


def _find_middle(out_dir: Path) -> Path | None:
    hits = sorted(out_dir.rglob("*_middle.json"))
    return hits[0] if hits else None


def _http_parse_zip(api_url: str, pdf: Path, out_dir: Path, log: Callable[[str], None]) -> Path:
    """POST the PDF to a warm `mineru-api` server's synchronous /file_parse and unzip the
    canonical output tree into out_dir (same layout the CLI writes). The server keeps models
    warm across a batch, so this avoids the per-PDF cold model load that the CLI pays.

    response_format_zip=true ⇒ the body is a zip whose internal paths are
    `<stem>/hybrid_auto/<stem>_middle.json` (rglob-found regardless of wrapper depth).
    """
    import zipfile

    try:
        import httpx
    except ImportError as e:  # pragma: no cover - httpx is a core dep
        raise RuntimeError("httpx not installed (needed for MINERU_API_URL warm-server mode)") from e

    url = api_url.rstrip("/") + "/file_parse"
    data = {
        "backend": "hybrid-engine",  # + parse_method auto == the CLI's hybrid-auto-engine
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
        "return_middle_json": "true",
        "response_format_zip": "true",
    }
    timeout = httpx.Timeout(connect=10.0, read=None, write=120.0, pool=10.0)  # read=None: sync parse blocks
    with open(pdf, "rb") as fh:
        r = httpx.post(
            url,
            data=data,
            files=[("files", (pdf.name, fh, "application/pdf"))],
            timeout=timeout,
            follow_redirects=True,
        )
    r.raise_for_status()
    if not r.content:
        raise RuntimeError("empty response body from mineru API")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmpzip = out_dir / f".{pdf.stem}.api.zip"
    tmpzip.write_bytes(r.content)
    with zipfile.ZipFile(tmpzip) as z:
        z.extractall(out_dir)  # trusted local server; layout = CLI -o tree
    tmpzip.unlink(missing_ok=True)

    middle = _find_middle(out_dir)
    if middle is None:
        raise RuntimeError("mineru API zip contained no *_middle.json")
    log(f"  · mineru via API {api_url} → {middle.name}")
    return middle


def _produce_middle(pdf: Path, out_dir: Path, log: Callable[[str], None]) -> Path:
    """Run MinerU on `pdf` into `out_dir` (warm API if MINERU_API_URL, else CLI) → its
    *_middle.json. No cache check — the caller decides when to reuse."""
    api_url = os.environ.get("MINERU_API_URL")
    if api_url:  # warm-server fast path; any failure falls through to the CLI below
        try:
            return _http_parse_zip(api_url, pdf, out_dir, log)
        except Exception as e:  # noqa: BLE001 — connection/HTTP/zip error → CLI fallback
            log(f"  · mineru API at {api_url} failed ({e!r}); falling back to CLI")

    bin_argv = find_mineru_bin()
    if bin_argv is None:
        raise SystemExit(_NOT_INSTALLED.format(venv=MINERU_VENV))

    cfg = _write_config()
    env = {**os.environ, "MINERU_TOOLS_CONFIG_JSON": str(cfg)}
    cmd = [*bin_argv, "-p", str(pdf), "-o", str(out_dir), "-b", MINERU_BACKEND]
    log(f"  · running mineru on {pdf.name} …")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError("mineru failed:\n" + "\n".join(tail))

    middle = _find_middle(out_dir)
    if middle is None:
        raise RuntimeError(f"mineru produced no *_middle.json under {out_dir}")
    return middle


def _run_subset(pdf: Path, cache_dir: Path, sel: list[int], log: Callable[[str], None]) -> Path:
    """Run MinerU on just the 0-based page indices `sel` (a --pages filter). Slices those
    pages into a temp PDF, runs MinerU on the slice, then **remaps** the slice's pdf_info back
    to original page indices (padding skipped pages with empty blocks) so page_markdown /
    parse_blocks key by original index. The remapped middle.json sits beside the slice's
    images/ dir, so figure `image_path`s still resolve. Not cached (a testing convenience)."""
    import fitz

    src = fitz.open(pdf)
    try:
        sel = [i for i in sel if 0 <= i < src.page_count]
        out_dir = cache_dir / "mineru_subset"
        out_dir.mkdir(parents=True, exist_ok=True)
        if not sel:
            empty = out_dir / f"{pdf.stem}.subset_middle.json"
            empty.write_text(json.dumps({"pdf_info": []}), "utf-8")
            return empty
        slice_pdf = out_dir / f"{pdf.stem}.subset.pdf"
        sub = fitz.open()
        try:
            for i in sel:
                sub.insert_pdf(src, from_page=i, to_page=i)
            sub.save(slice_pdf)
        finally:
            sub.close()
    finally:
        src.close()

    middle = _produce_middle(slice_pdf, out_dir, log)
    data = json.loads(middle.read_text("utf-8"))
    sliced = data.get("pdf_info", [])
    full: list[dict] = [{"para_blocks": []} for _ in range(max(sel) + 1)]
    for k, orig in enumerate(sel):
        if k < len(sliced):
            full[orig] = sliced[k]
    data["pdf_info"] = full
    remapped = middle.with_name(middle.stem + ".remapped.json")  # same dir → images/ resolves
    remapped.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    log(f"  · mineru --pages subset: {len(sel)} page(s), remapped to original indices")
    return remapped


def run_mineru(
    pdf: Path,
    cache_dir: Path,
    log: Callable[[str], None] = print,
    pages: set[int] | None = None,
) -> Path:
    """Run MinerU on `pdf` (idempotent) → path of the produced *_middle.json.

    Skips the subprocess when a middle.json already exists and is at least as new as the
    PDF. With `pages` (a 1-based --pages filter), MinerU runs on only those pages (sliced +
    index-remapped, uncached). Raises SystemExit with install instructions if mineru is
    unavailable.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    if pages:
        return _run_subset(pdf, cache_dir, sorted(p - 1 for p in pages), log)

    out_dir = cache_dir / "mineru_out"
    existing = _find_middle(out_dir)
    if existing and existing.stat().st_mtime >= pdf.stat().st_mtime:
        log(f"  · mineru cache hit: {existing.name}")
        return existing
    return _produce_middle(pdf, out_dir, log)


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


# ── per-page Markdown for the Outline path (ADR-0009) ────────────────────────────
# The Question path consumes parse_blocks (bbox geometry, to crop each question). The
# Outline path needs no geometry — one whole page = one Unit — only faithful per-page
# Markdown whose section headings survive as Markdown headings, because outline.finalize
# harvests the `N.N …` section number from `#`-prefixed lines. middle.json marks headings
# with a `type == "title"` block carrying a `level`, so we surface those as `#` headings;
# everything else (formula spans, text) reuses the same span reconstruction as parse_blocks.


def _block_markdown(blk: dict) -> str:
    """Markdown for a non-image para_block (image blocks are handled inline in page_markdown)."""
    text = _block_text(blk).strip()
    if not text:
        return ""
    if blk.get("type") == "title":
        level = blk.get("level") or 1
        try:
            level = max(1, min(int(level), 6))
        except (TypeError, ValueError):
            level = 1
        return f"{'#' * level} {text}"
    return text


def _image_of(blk: dict) -> tuple[str, str] | None:
    """(image_path, caption) for a MinerU image block, else None. The path is the figure's
    filename inside MinerU's `images/` dir; the caption is any `image_caption` sub-block text."""
    path: str | None = None
    caption = ""
    for sub in blk.get("blocks", []):
        is_caption = sub.get("type") == "image_caption"
        for line in sub.get("lines", []):
            for sp in line.get("spans", []):
                if sp.get("type") == "image" and sp.get("image_path"):
                    path = sp["image_path"]
                elif is_caption:
                    caption += sp.get("content", "") or ""
    return (path, caption.strip()) if path else None


def _fig_dest(page_index: int, k: int, src: str) -> str:
    """Page-scoped figure filename, e.g. page-0017.fig-1.jpg — colocated with the page's md
    (so `![](…)` refs stay valid), and moved as a set by outline.finalize."""
    ext = Path(src).suffix or ".jpg"
    return f"page-{page_index + 1:04d}.fig-{k + 1}{ext}"


def page_markdown(middle: Path) -> dict[int, str]:
    """middle.json → {page_index: markdown}, in reading order (ADR-0009/0010, Page/Outline).

    One paragraph per para_block; a `title` block becomes a `#` heading (so
    ``outline.section_of_page`` can read the section number), formula spans keep their
    ``$…$`` / ``$$…$$`` wrap (via ``_block_text``), and an image block becomes a Markdown
    image referencing its page-scoped figure filename (copied out by the strategy's emit).
    """
    data = json.loads(middle.read_text("utf-8"))
    out: dict[int, str] = {}
    for pi, page in enumerate(data.get("pdf_info", [])):
        parts: list[str] = []
        fig_k = 0
        for blk in page.get("para_blocks", []):
            if blk.get("type") == "image":
                got = _image_of(blk)
                if got:
                    src, caption = got
                    parts.append(f"![{caption}]({_fig_dest(pi, fig_k, src)})")
                    fig_k += 1
                continue
            md = _block_markdown(blk)
            if md:
                parts.append(md)
        out[pi] = "\n\n".join(parts)
    return out


def page_figures(middle: Path) -> dict[int, list[tuple[str, str]]]:
    """middle.json → {page_index: [(dest_filename, source_filename), ...]} (ADR-0010).

    dest_filename is the page-scoped name page_markdown referenced; source_filename is the
    figure inside MinerU's `images/` dir. The strategy's emit copies each source→dest into
    the Unit dir so the Markdown's `![](…)` resolves.
    """
    data = json.loads(middle.read_text("utf-8"))
    out: dict[int, list[tuple[str, str]]] = {}
    for pi, page in enumerate(data.get("pdf_info", [])):
        figs: list[tuple[str, str]] = []
        k = 0
        for blk in page.get("para_blocks", []):
            if blk.get("type") == "image":
                got = _image_of(blk)
                if got:
                    figs.append((_fig_dest(pi, k, got[0]), got[0]))
                    k += 1
        if figs:
            out[pi] = figs
    return out


# ── one-time installer (driven by `digest install-mineru`) ──────────────────────

_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# Known-good MLX pin for the vlm MLX engine. The MinerU maintainer reports mlx 0.31.2
# breaks it (a stream error); 0.31.1 works. Pinned exactly so a fresh install can't
# drift onto the broken release — revisit on any MinerU upgrade (ADR-0007).
_MLX_PIN = "mlx==0.31.1"


def install_mineru(log: Callable[[str], None] = print) -> None:
    """Build the isolated mineru venv + download all models (pipeline + VLM) via ModelScope.

    Heavy (~4 GB models); run explicitly, never implicitly. Uses a CN mirror for pip
    and ModelScope for models (HF is blocked/unreliable from CN — spike finding). The
    exam path runs MinerU's `hybrid-auto-engine`, which needs BOTH the pipeline models
    and the MinerU2.5-Pro VLM (MLX-accelerated on Apple Silicon), hence `-m all` and the
    pinned `mlx` (ADR-0007).
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
        log(f"installing mineru[all] + {_MLX_PIN} (CN mirror) …")
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
                _MLX_PIN,
            ]
        )
    else:
        log(f"mineru venv already present at {MINERU_VENV}; skipping install.")

    cfg = _write_config()
    env = {**os.environ, "MINERU_TOOLS_CONFIG_JSON": str(cfg)}
    log("downloading all models (pipeline + VLM) from ModelScope …")
    subprocess.run(
        [str(MINERU_BIN_PATH.parent / "mineru-models-download"), "-s", "modelscope", "-m", "all"],
        env=env,
        check=True,
    )
    log(f"✓ mineru ready: {' '.join([str(MINERU_BIN_PATH)])} (version {mineru_pkg_version()})")
