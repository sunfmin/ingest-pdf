"""The Manifest (CONTEXT): per-run record for idempotent resume + provenance.

Resume granularity is the page — the atomic VLM call (ADR-0003). A page's Units
are produced together from that one call and recorded under it, so re-running
never re-burns the GPU on a page that already completed. Saved atomically after
every page so a crash / Ctrl-C leaves a consistent file to resume from.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {"tool": "digest-pdf", "model": {}, "pdfs": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text("utf-8"))
            except Exception:
                pass  # corrupt/partial manifest -> start fresh, re-do everything
        self.data.setdefault("pdfs", {})

    @staticmethod
    def source_sig(pdf_path: Path) -> dict:
        st = pdf_path.stat()
        return {"size": st.st_size, "mtime": int(st.st_mtime)}

    def set_model(self, model_id: str, revision: str, dpi: int) -> None:
        with self._lock:
            self.data["model"] = {"id": model_id, "revision": revision, "dpi": dpi}
            self._save_locked()

    def ensure_pdf(self, pdf_key: str, strategy: str, sig: dict, model: str | None = None) -> None:
        """Register a PDF; reset its pages if source, strategy, or model changed (stale).

        `model` is the per-PDF segmentation+transcription model (ADR-0006): for the
        VLM-driven strategies it equals the VLM id; for Question (zero-VLM) it is the
        MinerU id. A model change invalidates provenance, so it resets like a strategy
        change. Stored per-PDF so a single run mixing strategies stays self-consistent.
        """
        with self._lock:
            pdfs = self.data["pdfs"]
            rec = pdfs.get(pdf_key)
            stale = (
                rec is None
                or rec.get("source") != sig
                or rec.get("strategy") != strategy
                or rec.get("model") != model
            )
            if stale:
                pdfs[pdf_key] = {"strategy": strategy, "source": sig, "model": model, "pages": {}}
            self._save_locked()

    def page_done(self, pdf_key: str, page_index: int) -> bool:
        with self._lock:
            rec = self.data["pdfs"].get(pdf_key)
            if not rec:
                return False
            page = rec["pages"].get(str(page_index))
            return bool(page and page.get("status") == "done")

    def mark_page(self, pdf_key: str, page_index: int, status: str, units: list[dict]) -> None:
        with self._lock:
            rec = self.data["pdfs"][pdf_key]
            rec["pages"][str(page_index)] = {"status": status, "units": units}
            self._save_locked()

    def save(self) -> None:
        """Persist current state (used by the Outline finalize pass, ADR-0004)."""
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, self.path)
