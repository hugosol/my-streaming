# 01: Bilingual SRT 生成 seam（S1）与确定性校验骨架

**Delivers:** enabling: unblocks P1–P12
**Blocked by:** None (can start immediately)
**Status:** ready-for-agent

**Parent:** `.scratch/subtitle-local-alignment-repair/contract.md`（Seam S1；Spec: `.scratch/subtitle-local-alignment-repair/PRD.md`）

## What to build

把现有「把带时间的英文字幕与 Translation Chunk 中文结果组装成 Bilingual SRT」的职责加深为一个 module，并让**正常翻译路径**与**重试继续路径**都只经过这一个 seam（contract S1）。这是本功能的 prefactor：先把改动变简单，后续切片才有一个能同时被两条路径观察的观察点。

Module 的 interface（S1，形状由实现决定，但能力必须一致）：

- **接收**：修复前的带时间英文字幕（字幕块数量、每块英文文本、起止时间码）、有序且保留空白的 Translation Chunk 中文结果（每块一行，中文空缺为空行）、由外部注入的模型调用依赖。
- **交付**：实际 Bilingual SRT 文件；或原有输入／组装失败结果。
- **隐藏**：句组选择、请求格式、候选解析、英文保全校验、时间计算、整组选择与回退、最终组装。
- 调用者**不**分别提交新英文、新中文、新时间——一致性责任留在 module 内。

本票不实现任何修复行为，只落地 seam 与结构校验骨架：句组重建（与翻译阶段同一「英文句末标点」划分规则，块序与编号保持）、块数/英文内容保全/句组范围/时间合法性这四类确定性校验的入口，以及"失败即保留原输入并向上报告失败"的既有语义。

## Acceptance criteria

- [ ] 正常翻译路径与重试继续路径都通过同一个 module 交付 Bilingual SRT；没有任何调用点自行拼装英文、中文与时间。
- [ ] 在不启用修复行为的情况下，对同一份「可用 SRT + 中文结果」夹具，module 产出的 Bilingual SRT 与改动前实现的产出一致：字幕块数量、每块英文文本、中文行落位、时间码、块编号全部相同。
- [ ] module 能在临时目录中使用真实字幕与真实 Translation Chunk 产物运行，并把 Bilingual SRT 写回磁盘供检查；不通过 mock 文件写入来证明最终字幕正确。
- [ ] module 按翻译阶段相同的规则从英文行重建句组，句组数量、顺序与块归属与翻译阶段一致。
- [ ] 结构性校验对后续切片可用且被实际调用：块数、英文内容保全（空白归一化不能掩盖单词差异）、句组范围、时间合法性。
- [ ] 输入不一致或组装失败时保留原字幕输入，不产出半成品 Bilingual SRT，并向上报告失败（沿用既有组合失败语义）。
- [ ] 现有回归保障保持通过：较短中文句组保留中文空白行且不把下一句译文上移（`tests/test_translate_alignment.py`），末尾空白行往返读写仍成立。
- [ ] 不新增用户可见 Stage、Job Status 或 Progress 语义。

## Coverage partition

本票为 enabling 切片，不单独占有任何 promise 的验收项；它的存在使 P1–P12 在两条路径上可被观察。P1、P2、P6、P8 由 02 交付；P4、P5、P7 由 03 交付；P9、P10 由 04 交付；P11、P12 由 05 交付并持有测试；P3 由 06 交付并持有测试。
