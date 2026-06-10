# My Streaming

本地视频流媒体服务，支持 YouTube 字幕下载、标点修复、重新分句、AI 翻译。

## Language

### 核心实体

**Job**:
一次 YouTube 视频下载和字幕处理流水线的执行实例。
_Avoid_: 任务、Task

**Video**:
视频目录下的一个 MP4 文件，通过 `VideoEntry` 暴露给前端播放。
_Avoid_: 影片、媒体文件

**Video ID**:
视频文件名的 MD5 前 8 位十六进制，用于唯一标识一个视频。
_Avoid_: 视频编号、文件哈希

### 流水线阶段

**Stage**:
Job 在流水线中所处的阶段，严格按序推进：

| 值 | 含义 |
|---|---|
| `pending` | 等待处理 |
| `downloading` | 下载中 |
| `punctuating` | 标点处理中 |
| `resegmenting` | 重新分句中 |
| `translating` | 翻译中 |
| `finalizing` | 收尾中 |
| `done` | 完成 |

_Avoid_: Phase、步骤

**Status**:
Job 的进行/终态，只取三个值：`in_progress`（进行中）、`success`（成功）、`failed`（失败）。

**Progress**:
翻译阶段内的进度，格式 `A/B`，A 为已完成的 chunk 数，B 为总 chunk 数。其他阶段为空。
_Avoid_: 完成度、百分比

### 翻译子概念

**Chunk**:
字幕文本按约 100 行切分的翻译单元。每个 chunk 对应两个文件：`chunk_NNN.txt`（英文原文）和 `chunk_NNN_chinese.txt`（中文翻译结果）。
_Avoid_: 分块、片段

**Workspace**:
翻译阶段的中间产物目录，位于 `jobs/<job_id>/<srt_stem>_workspace/`，包含 chunks 子目录和聚合后的文本文件。
_Avoid_: 工作区、临时目录

### 输出产物

**Bilingual SRT**:
`combine-subtitles.ps1` 合并原文和中文后生成的双语字幕文件，命名为 `Bilingual_<原名>.srt`。`finalize-subtitles.ps1` 将其替换为原文件名，原文件备份为 `<原名>-src.srt`。
