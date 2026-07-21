# Question 策略用 MinerU 做切分+转写：行使 ADR-0003 的 per-strategy 修订（零 VLM）

> **由 ADR-0007 修订**：MinerU 现跑的是**打包的 `MinerU2.5-Pro-2605-1.2B` VLM**（`-b hybrid-auto-engine`，Apple Silicon 上自动 MLX），而非本文假定的 pipeline 模型。因此 `install_mineru` 改为 `-m all` + 钉 `mlx==0.31.1`，provenance 记实际模型名（`MinerU2.5-Pro-2605-1.2B@mineru<ver>-hybrid`）而非 `mineru@<ver>`。本文的核心决定不变（MinerU 独任切分+转写、`para_blocks` 单源、子进程隔离）。惟「零 VLM」应读作**零项目自管 VLM token**——MinerU 子进程内部现在本身就是一个 VLM。

## 状态

Accepted（milestone 4 / issue #4）。

## 背景

ADR-0003 定「每页**一次** VLM 调用，同时返回整页转写 + 逐题边界框」，理由是单 GPU 是吞吐天花板、calls-per-page ≈ 墙钟。但这条在**密集的试卷**上不可靠：单次要同时转写+grounding，dense 页面易错位/漏框。叠加 ADR-0005 的事实——转写模型 NuExtract3 **不做 grounding**，边界框本就要**另起一次** Qwen3-VL 调用——于是「一次调用」在试卷上本来就不成立。

外部 spike（M4 Pro / 48GB，对 2024 新课标Ⅰ数学**纯扫描**6 页 + 全文 23 页）实测三家版面/检测方案：

- **MinerU**（hybrid 后端，MPS）：扫描 6 页 **11/11** 按题切对、公式识别内联、跨页竖拼正确；稳态单页 <1s。
- **PP-StructureV3**（PaddleX）：中文 OCR/版面/公式同级，但本机 Mac **CPU 跑 >10min/6 页**（含神经网络去弯曲），不可用。
- **YOLO**：无「题目区域」预训练模型（DocLayout-YOLO 只分文档块），需自训，开箱最差。
- 现有**纯文字层**切题器对扫描件**无能为力**（无文字层）。

MinerU 同时给出**框**与**带 LaTeX 的 OCR 文本**（`middle.json` 的 `para_blocks`：bbox 在 PDF 点空间；公式 span `inline_equation` 的 content 是干净 LaTeX）。即它能**独自**充当切分 + 转写两源，让试卷路径**完全不需要 VLM**。

## 决定

1. **Question 策略以 MinerU 为唯一切分+转写源**，`needs_vlm=False`，试卷路径**零 VLM 调用**——正式行使 ADR-0003「May be revisited per-strategy if a document type proves too dense」的口子。MinerU 走**子进程 + 隔离环境**（`install-mineru` 建独立 uv venv，经 ModelScope 取模型），核心 `.venv` 保持轻量，与 `vlm` 可选 extra 同哲学。
2. **重定义 Transcription**（CONTEXT）为「由**策略**提供的识别文本」——VLM 转写或版面 OCR 视策略而定；**image 仍为证据**不变。Question 策略的转写 = MinerU 重建文本（text span 直拼 + `inline_equation` 包 `$…$` + 行间公式包 `$$…$$`），单源 `para_blocks`（坐标与文本同构，规避 `content_list.json` 的坐标偏移陷阱）。
3. **provenance 记切分/转写模型**：逐 Unit 头为准（`RunContext` 取策略的 `model_id`/`revision`，缺省回退 VLM）；manifest 增 **per-pdf `model` 字段**（与 strategy/source 同列，变化即重置），顶层 `model` 仍记 VLM 作向后兼容的「主模型」。
4. **跨页题**经策略 `finalize` 装配（同题号碎片的图竖拼 + 文拼接 → 单 Unit），范式承 ADR-0004 的 Outline `finalize`；管线把「跑 finalize」从 outline 专例**泛化**为按策略收集。
5. **检测**用廉价**文字层**启发式（大题头 `^[一二三四…]+、` + 左边距题号标记 → Question；章节号标题密度高 → Outline；否则 Page），**不跑 VLM/MinerU**（承 ADR-0002「detection is a cheap heuristic」）；**纯扫描**文字层为空 → 落到 Page，由 `--strategy question` 覆盖。

## 考虑并否决

- **VLM 整页转写 + MinerU 出框**（双模型）：仍每页烧一次 VLM，与「切题/OCR 零 token」相悖，且双模型 provenance 更繁。
- **逐 crop 调 VLM 转写**：N× 负载，直接违背 ADR-0003 的成本理由；仅当标定证明整页单次转写不可靠才考虑。
- **MinerU 进核心/可选 extra 进程内 import**：污染轻量 `.venv`，torch/paddle 与 mlx-vlm 共存未验证，且继承国内镜像 + ModelScope 的安装摩擦。

## 后果

- MinerU 成为**可选重型依赖**，安装隔离在 `ingest install-mineru`（镜像 + ModelScope 配置 + 模型下载）；首次 `--strategy question` 找不到 mineru 时给清晰报错指引。
- 切题启发式**仍需标定**：换版式/换源须重跑标定，否则「免逐题人核」失效；MinerU 缺失或切分失败 → 回退 `--strategy page`。
- 管线新增 `needs_vlm` 旁路与 `_VLM_SKIP` 哨兵（区分「VLM 主动跳过」与「VLM 失败」，后者仍记 failed）。
- `para_blocks` 公式 span 若经标定发现劣于 `content_list`，回退为「content_list 文本 + 与 para_blocks 按 bbox-IoU 对齐」——记为 follow-up，不阻塞首版。
- 真高考 PDF **不入库**（版权/体积）；e2e 走外部路径，仓库测试用合成 fixture。spike 真卷数字：扫描 11/11、全文 18/19（漏的 Q11 = 合并块吞题号，已由加固启发式的「合并块回退」覆盖）。
