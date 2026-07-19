"""VLM workers: a milestone-1 stub and the real Qwen3-VL worker (milestone 2).

Both expose the same interface the pipeline depends on:
    .model_id : str
    .revision : str
    .transcribe(rendered: RenderedPage) -> PageResult

The real worker loads the model once and stays resident (warm) — the pipeline's
single VLM thread calls .transcribe() back-to-back, so the model never reloads
(ADR-0001). Decoded at low temp + repetition penalty; temp=0 degenerates in this
mlx-vlm stack (ADR-0001, validated by the smoke test).
"""

from __future__ import annotations

from ..models import PageResult, RenderedPage
from .postprocess import clean, model_revision
from .prompt import TRANSCRIBE_PROMPT

DEFAULT_MODEL = "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"


class StubVLM:
    """Fixed-output placeholder to prove the pipeline without a model."""

    model_id = "stub"
    revision = "m1"

    def transcribe(self, rendered: RenderedPage) -> PageResult:
        job = rendered.job
        md = (
            f"# {rendered.png_path.stem}\n\n"
            f"_Stub transcription (milestone 1 — VLM not wired yet)._\n\n"
            f"- source page: {job.page_index + 1}\n"
            f"- image: `{rendered.png_path.name}` ({rendered.width}×{rendered.height})\n"
        )
        return PageResult(markdown=md, questions=[])


class MlxVLM:
    """Real local worker over mlx-vlm. Loads the model once (resident)."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        repetition_penalty: float = 1.05,
        max_tokens: int = 4096,
    ):
        try:
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config
        except ImportError as e:  # pragma: no cover
            raise SystemExit(
                "mlx-vlm is not installed. Install the VLM extra:\n"
                "    uv sync --extra vlm      (or)  uv run --extra vlm ingest ...\n"
                f"(import error: {e})"
            )

        self._generate = generate
        self._apply_chat_template = apply_chat_template
        self.model_id = model_id
        self.temperature = temperature
        self.repetition_penalty = repetition_penalty
        self.max_tokens = max_tokens

        # Load ONCE — stays resident for the whole run (warm-model pipeline).
        self.model, self.processor = load(model_id)
        self.config = load_config(model_id)
        self.revision = model_revision(model_id)

    def transcribe(self, rendered: RenderedPage) -> PageResult:
        formatted = self._apply_chat_template(self.processor, self.config, TRANSCRIBE_PROMPT, num_images=1)
        result = self._generate(
            self.model,
            self.processor,
            formatted,
            image=[str(rendered.png_path)],  # mlx-vlm 0.6.5 wants a file path, not a PIL image
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            repetition_penalty=self.repetition_penalty,
            verbose=False,
        )
        text = getattr(result, "text", str(result))
        return PageResult(markdown=clean(text), questions=[])
