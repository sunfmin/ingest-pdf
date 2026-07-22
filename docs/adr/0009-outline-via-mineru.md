# Outline 增加 MinerU 转写变体（`outline-mineru`）：向单一识别引擎收敛

## 状态

Accepted（本次）。**部分修订 ADR-0005 / ADR-0007**：NuExtract3 不再是课本路径**唯一**的转写器；把 `outline-mineru` 提为默认、或让 auto 把扫描课本路由到它，仍**受一道跨轮稳定性标定的闸门**约束（见下）。

## 背景

一次审查暴露出「试卷 / 课本」两条路在**用户面**上的不对称：

- **试卷（Question）** → MinerU：有一等公民安装命令 `install-mineru`、独立隔离 venv、skill Phase A 依 `needs_mineru` 自动就绪。开箱即用。
- **课本（Outline）** → mlx VLM（NuExtract3）：是可选 extra，**没有** `--install-vlm`，skill Phase A **不管** `needs_vlm`，缺失时运行期 `SystemExit`。

内部**隔离机制**不同是有理由的（ADR-0006「考虑并否决」：MinerU 的依赖闭包与核心 venv/mlx-vlm 冲突，必须子进程隔离），但**引导体验**不该不同。更深一层的问题是：**为什么课本要用第二个识别引擎？**

在人教 A 版《必修 第一册》p13–22（跨 1.2→1.3 边界）上实测 MinerU vs NuExtract3：

- **建树等价**：两者都把 `1.2` 判在 p14、`1.3` 判在 p17；outline 的 `section_of_page` 对两者的输出建出**相同**的 `第N章/<section>/` 树。
- **插图**：MinerU 把 Venn 图（图1.3-1）裁出并链接；NuExtract3 直接丢图。（注：outline 模式下每个 Unit 的 image 半边本就是**整页**渲染，图始终在页图里——md 内联图只是锦上添花。）
- **速度**：MinerU 稳态远快于 NuExtract3 的 ~33s/页（全书 270 页 ≈ 2.5h vs 十几分钟量级）。
- **关键**：ADR-0005 为 NuExtract3 背书的标定只比过 **NuExtract3 vs Qwen3-VL**，**从未**与 MinerU2.5 比过。「课本得用 mlx-vlm」是个未验证的历史假设——NuExtract3 先落地（ADR-0001→0005 做转写），MinerU 后引入（ADR-0006/0007 只为试卷切题的 grounding），课本路径此后没回头合并。

关键的实现可行性：MinerU 的 `middle.json` 的 `para_blocks` **带 `type:"title"` 且有 `level`**，所以「让节标题成为 Markdown 标题」这件 outline 建树必需的事，能在**不破坏 ADR-0006「middle.json 单源」纪律**的前提下做到。

## 决定

1. **新增 `outline-mineru` 策略**（`OutlineMineruStrategy`，`needs_vlm=False`，与 Question 同哲学的零-项目-VLM 路径）：MinerU 逐页转写，`emit` 产一个整页 Unit（`page-NNNN`）；**原样复用 `outline.finalize`** 建 `第N章/<section>/` 树——建树的采集器**与转写器无关**（只认 `#…\d+\.\d+`）。
2. **`_mineru.page_markdown()`**：从 `middle.json` 的 `para_blocks` 按阅读序重建每页 Markdown——`title` 块按 `level` 加 `#`（供 `section_of_page` 采到节号），公式 span 沿用 `_block_text` 的 `$…$`/`$$…$$` 包裹，`image` 块跳过。与 `parse_blocks`（Question 用的 bbox 几何视图）并列，各取所需、同一单源。
3. **布线**：`detect.get_strategy`、`--strategy` 选项、`--inspect` 的 `needs_mineru`/estimate、Layout Spec 的 `STRATEGY_TOKEN`（`outline-mineru` 与 `outline` 同用 `{section}` token）。**auto 检测不变**——扫描课本无文字层，廉价启发式（ADR-0002）识不出，故 `outline-mineru` 经 `--strategy` 或 Layout Spec 显式选用。
4. **provenance** 记 MinerU 的模型身份（`model_identity()`，同 Question）。
5. **不退役 mlx VLM 的 outline 路径**。把 `outline-mineru` 提为默认、或 auto 路由扫描课本到它，**闸门 = 一道跨轮稳定性标定**：同批页 MinerU 跑 ≥2–3 轮、diff 节标题/树是否恒定。这正是 ADR-0005 当年选 NuExtract3 的**唯一**硬理由（4-bit 解码抖动会让树分叉），对 Miner（也是温度解码的 VLM）尚未复测。闸门通过前两者并存。

## 考虑并否决

- **直接让 `outline` 改用 MinerU / 现在就删掉 NuExtract3**：否决。MinerU 节标题的跨轮稳定性未测；不在过闸前拆掉已标定的路径。
- **正交的 `--transcriber {vlm,mineru}` 开关**（转写器独立于切分策略）：概念上更干净，但要把「转写器」贯穿 pipeline，改动大；本次取**最小可复审**的独立策略，镜像既有 Question/MinerU 范式。留作后续。
- **改解析 `content_list.json`**（直接带 heading level + 图路径）：否决。为守 ADR-0006「middle.json 单源」、并避免引入 content_list 的像素空间坐标；`middle.json` 的 title 块本就带 `level`，够用。

## 后果

- **图内联**是 follow-up：现版课本 md 不内联 MinerU 裁出的图（与 mlx VLM outline 路径的丢图行为持平，ADR-0005），但每个 Unit 的**整页图**始终保留插图。要把图搬进 md，需经 content_list 做 页↔图文件 映射。
- **skill Phase A** 现在依 `needs_mineru`（`--inspect` 对 `outline-mineru` 报 true）为课本也自动就绪 MinerU，**关上了本次审查发现的试卷/课本不对称**。
- **`--pages` 仍会在 `plan()` 里触发整册 MinerU**（与 Question 共此限制）；测试时先切页 PDF 规避，本次不修。
- **标定闸门**：过闸前 `outline-mineru` 是显式可选项，非默认；`outline`（NuExtract3）保持不动。
- **测试**：合成 `middle.json` + monkeypatch `run_mineru`/`model_identity`，全程无模型（`tests/strategies/test_outline_mineru.py`）——覆盖 `page_markdown` 的标题/公式/图/多页，以及端到端建树 + 零-VLM 断言。
