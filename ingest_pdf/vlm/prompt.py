"""Prompts for the VLM. General-purpose transcription (works for any PDF, not
just exams) — question-boundary grounding is added in milestone 4.
"""

# Full-page transcription → Markdown + LaTeX. Mirrors the prior impl's textbook
# prompt but domain-neutral. Figures are stripped in postprocess (the model
# hallucinates their content), so we don't ask for image descriptions.
TRANSCRIBE_PROMPT = (
    "将这一页完整转录为 Markdown。保留标题层级、段落和列表；"
    "数学公式一律用 LaTeX（行内 $...$，独立公式 $$...$$）；"
    "表格优先用 Markdown 表格，只有需要合并单元格（跨行/跨列）时才用 HTML <table>，"
    "且 HTML 表格内不要写数学公式；略去页眉、页脚和页码。"
    "只输出 Markdown 正文，不要任何解释、不要代码围栏。"
)
