# MinerU 升级为 VLM 后端（MinerU2.5-Pro-2605 + MLX）：声明式复现 + provenance 记实际模型（修订 ADR-0006）

## 状态

Accepted。修订 ADR-0006 的**模型/后端/安装/provenance** 部分；ADR-0006 的核心决定（MinerU 独任切分+转写、试卷路径零**项目** VLM token、子进程隔离、`para_blocks` 单源）不变。

## 背景

ADR-0006 假定 MinerU 走 **pipeline 模型**（`mineru-models-download -m pipeline`，ModelScope），provenance 记 `mineru@<pkg 版本>`。现实已漂移：

- 隔离 venv 现为 **mineru 3.4.4**，`mlx 0.31.1` / `mlx-vlm 0.3.9`；`-b hybrid-auto-engine` 在 Apple Silicon（macOS 13.5+）**自动选 MLX 引擎**，跑的是 mineru 3.4.4 **打包默认的 `MinerU2.5-Pro-2605-1.2B` VLM**（Qwen2-VL 架构，2.2 GB fp16，已在 `~/.cache/modelscope` 缓存）。
- 产出 `*_middle.json` 标 `_backend: hybrid`、`_version_name: 3.4.4`；`para_blocks`（bbox 仍在 PDF 点空间、公式 span 仍带干净 LaTeX）**结构不变**，`parse_blocks` 照常消费。

即：用户诉求「换用 `MinerU2.5-Pro-2605-1.2B` 的 mlx 版」在**运行时已是现状**——没有第三方 HF mlx repo 需要指向（社区只有单用户 `usermma/…-mlx-fp16` 之类），也没有旧模型可替。真正的问题是**运行现状与仓库声明之间的漂移**（single source of truth）：`install_mineru` 仍写未钉的 `mineru[all]` + `-m pipeline`，全新机器据此装出的环境未必能复现 Pro-2605 + MLX；且 provenance 只记包版本，看不见实际识别模型。

关键约束：MinerU **没有**「每次运行选特定 VLM 版本」的开关——VLM 模型版本随 `mineru` 包版本捆绑（3.3 起为 Pro-2605）。所以「用 Pro-2605」本质是**包版本**问题，不是「换 HF repo」问题。

## 决定

1. **声明式复现运行现状（运行行为不变）。** `install_mineru`：
   - pip 由 `mineru[all]` 改为 `mineru[all]` **+ 钉 `mlx==0.31.1`**（维护者证实 `0.31.2` 会挂 MLX 引擎，stream error）；
   - 模型下载 `-m pipeline` → **`-m all`**（`hybrid-auto-engine` 需 pipeline 模型**与** VLM 模型两套）；
   - 后端抽成单一常量 `MINERU_BACKEND = "hybrid-auto-engine"`，供 argv 与 provenance 共用。
2. **provenance 记实际模型。** `model_identity()` 从磁盘模型缓存探测实际 VLM 名（`MinerU2.5-Pro-2605-1.2B`），`revision = mineru<pkg 版本>-<backend>`（如 `mineru3.4.4-hybrid`）；探测失败回退 `("mineru", <pkg 版本>)`。每 Unit 头因此写 `MinerU2.5-Pro-2605-1.2B@mineru3.4.4-hybrid`。模型名或后端任一变化即使 provenance 失效；又因换模型必伴随换包版本，钉包版本已足以让任何模型变更触发**重标定**（承 CONTEXT「model id/revision 变即重跑 Calibration」）。
3. **标定已过。** 在启用「命名模型」标签前，对现网 Pro-2605/hybrid 产出做了 Calibration 抽检（《2015 年浙江高考数学【文】（解析版）》p1 选择题 ×8 + 选项 + p5 解答区）：题切分、选项、公式 LaTeX、`考点/分析/解答/点评` 结构均**忠实**，无捏造 / 漏题 / 子句错位（ADR-0005 记的 Qwen 失效模式均未出现）。仅见 cosmetic OCR 抖动（Q2 选项 C 的 `cm³` 上标被读成 `cm^{\le} 8`、Q3 引号变体），不影响切题与解答文本。据此授予 `MinerU2.5-Pro-2605` 标定标签。

## 考虑并否决

- **指向第三方 HF mlx port**（`usermma/MinerU2.5-Pro-2605-1.2B-mlx-fp16`、`carlesonielfa/…-mlx-{4,8}bit`）：单用户上传、只 fp16 / 未经官方 mlx-community 校验，且要绕开 MinerU 自己的模型管理；48 GB M4 Pro 上 1.2B 无内存/速度压力，量化无收益，脆弱。
- **切纯 `vlm-auto-engine`（去掉 pipeline 半边）**：会**改变运行时产出**，须 A/B + 重标定；hybrid 是 ADR-0006 spike 实测选定的后端，不在本次「仅声明式复现」范围内。
- **只在 revision 追加 backend、不记模型名**：改动最小，但 Unit 头看不出实际是 Pro-2605，违背 CONTEXT「model id + revision」。

## 后果

- 全新机器 `ingest install-mineru` 现可**复现** Pro-2605 + MLX 现状（钉 `mlx`、下 `-m all` 模型）。
- provenance 的 model id 由 `mineru@3.4.4` 变为 `MinerU2.5-Pro-2605-1.2B@mineru3.4.4-hybrid`；旧 manifest 里 `mineru@…` 记录会被视为 stale（per-pdf `model` 字段变化即重置，承 ADR-0006 决定 3）——重跑即以新标签重建，运行产出不变。
- `mlx==0.31.1` 是**已知债**：MinerU 升级可能要求更新的 mlx；升级时须重验 MLX 引擎并移动此钉（代码注释与安装文案已标）。
- 「零 VLM」的语义需正名：ADR-0006 说的「零 VLM」指**零项目自管 VLM 调用 / 零 VLM token**（ADR-0001/0005 的 mlx-vlm 栈）；MinerU 子进程内部现在**本身就是一个 VLM**（MinerU2.5-Pro）。黑盒边界不变，成本论点（试卷路径不烧项目 VLM token）不变。
