# MinerU 成为唯一转写引擎,退役 mlx VLM(NuExtract3)

## 状态

Accepted（本次）。**接替/修订 ADR-0001（warm VLM 单-GPU 流水线）、ADR-0003（每页一次 VLM 调用）、ADR-0005（NuExtract3 转写模型）；承接 ADR-0009。**

## 背景

ADR-0009 加了 `outline-mineru`(MinerU 转写课本 + 复用 outline 建树),并把「提为默认 / 退役 mlx-vlm」明确闸在**一道跨轮稳定性标定**之后——那是 ADR-0005 当年选 NuExtract3 的唯一硬理由(转写一抖,outline 树就分叉)。

标定已跑(人教 A 版《必修 第一册》p13–22,跨 1.2→1.3 边界,**3 轮各自独立的 MinerU 子进程,无缓存复用**):

- **树逐页一致**:10 页每一页在 3 轮里都落进同一个 `第N章/<section>/` 目录。
- **逐页转写相似度 = 1.000**(difflib,A↔B、A↔C 全页):MinerU2.5 是确定性解码,跨轮**逐字节相同**——反超 NuExtract3 自己 0.968 的稳定度。

叠加 ADR-0009 已证的:与 VLM 路径**同节边界**、**保住插图**(整页 Unit 图始终含图)、**快一个数量级**(全书 270 页 ~十几分钟 vs ~2.5h)。闸门通过,于是把转写收敛到单一引擎。

## 决定

1. **MinerU 是 page / outline / question 的唯一转写引擎**。`PageStrategy` 改为 MinerU 版(`needs_vlm=False`,`plan()` 跑一次 MinerU、`_mineru.page_markdown` 出每页 markdown、`emit` 产整页 Unit);`OutlineStrategy` **继承** `PageStrategy` 再加建树 `finalize`;ADR-0009 的 `outline-mineru` 名**折叠回 `outline`**。
2. **auto 兜底 page→outline**(ADR-0002 的廉价文字层启发式不变):非试卷文档一律 outline。`outline.finalize` 在**零节标题**时**优雅退化为扁平 `page-NNNN`**(不给非课本强建空树)。`page` 仅经显式 `--strategy page` 到达(强制扁平)。→ **扫描课本 auto 自动出树**,其它 auto 自动扁平。
3. **删除 mlx VLM 全栈**:`MlxVLM`、NuExtract3 默认模型、`vlm/prompt.py`、`vlm/postprocess.py`、CLI 的 `--stub`/`--model`/`--temperature`/`--repetition-penalty`/`--max-tokens`、`cli._make_vlm`/`_needs_vlm`;`pyproject` 去掉 `vlm` 可选 extra(mlx-vlm 从 lock/venv 移除)。保留 `NoVLM` 作零-VLM 哨兵 + 顶层 provenance。
4. **每页 markdown** 由 `_mineru.page_markdown`:title 块按 `level` 成 `#` 标题(供 `section_of_page` 建树),公式 span 保 `$…$`/`$$…$$`,守 middle.json 单源(ADR-0006/0009)。

## 考虑并否决

- **保留 mlx VLM 作可选后备**:否决。两个转写器 = 两份标定 + 两条安装 + 那个试卷/课本不对称;MinerU 在此语料已反超,留着只是负担与漂移源。真需要换模型时,`--install-mineru` 一条路即可。
- **本次就砍掉 pipeline 的 VLM 线程**:否决。线程结构(render→vlm→write,ADR-0001)经验证;零-VLM 走既有 `_VLM_SKIP` 分支,正确且无害。留为空转 passthrough,把它删干净列为纯机械 follow-up,降低本次退役的风险面。

## 后果

- **核心装轻**(无 mlx/torch);唯一重依赖是 MinerU 隔离 venv(`ingest --install-mineru`)。page/outline/question 统一经它 → skill Phase A 依 `--inspect` 的 `needs_mineru` 一视同仁就绪,**试卷/课本安装不对称彻底消除**(本轮调查的起点)。
- **ADR-0001** 的「warm 单-GPU VLM 流水线」raison d'être 消失,其 VLM 线程降为 passthrough;**ADR-0003**「每页一次 VLM 调用」moot;**ADR-0005** 的 NuExtract3 退役(其标定证据留作历史)。三者头部加了指回本 ADR 的注记。
- **标定 caveat 延续**(承 ADR-0005 原文):MinerU 的稳定/保真只在**一册一边界、3 轮**验证过;拓到其它册/科目/born-digital PDF 时**仍须重跑标定**。
- **auto 语义变化**:任何非试卷文档 → outline(有节标题出树,无则扁平)。**扫描试卷**仍需显式 `--strategy question`(detect 无文字层无从区分扫描试卷与其它扫描件——ADR-0002 的既有限制,未变)。
- **Follow-up**(部分承 ADR-0009):① 图内联(把 MinerU 裁出的图搬进 md);② `--pages` 仍触发整册 MinerU(与 Question 共此限制);③ 删掉 pipeline 里空转的 VLM 线程,简化为 render→write。
