# PRD: Job 重试与删除

**Status:** `ready-for-agent`

## Problem Statement

当前流水线（下载 → 标点 → 断句 → 翻译 → 收尾）中的 Job 一旦失败，用户只能看到失败状态和错误信息，无法对失败 Job 做任何操作。用户需要一种方式：要么清理失败的 Job 以重新提交，要么从翻译阶段的断点重试，而不必从头开始整个流水线。

## Solution

为失败 Job 提供两个操作：

1. **删除**：对任意阶段失败的 Job，删除数据库记录和对应的 `jobs/<job_id>/` 文件目录，释放磁盘空间，允许用户重新提交。
2. **重试**：仅对翻译阶段失败的 Job，从断点恢复——保留已成功翻译的 Chunk，仅重新处理失败的 Chunk，完成后继续走收尾流程。

## User Stories

1. 作为用户，我希望能删除一个失败的 Job，以便清理磁盘和数据库，重新提交同一个视频。
2. 作为用户，我希望能删除处于任意阶段（下载、标点、断句、翻译、收尾）的失败 Job。
3. 作为用户，我希望能重试一个翻译阶段失败的 Job，利用已有的成功 Chunk 从断点恢复，避免从头翻译。
4. 作为用户，我希望能看到重试过程中的实时进度（A/B 格式），以便了解重试进展。
5. 作为用户，我希望重试再次失败后仍能再次重试，进度保留，支持多次断点续传。
6. 作为用户，我希望进行中（`in_progress`）的 Job 不显示任何操作按钮，避免误操作。
7. 作为用户，我希望翻译阶段外的失败 Job 只显示删除按钮，不显示重试按钮。
8. 作为用户，我希望成功完成的 Job 不出现在任务列表中，而是以视频形式出现在下方视频列表。
9. 作为用户，我希望删除操作有明确的反馈，Job 从列表中消失，文件目录被清理。
10. 作为用户，我希望点击重试或删除按钮后不会因为连点而触发重复操作。

## Implementation Decisions

### 1. 数据库 Schema 重构

- 新增 `stage` 列，记录 Job 当前所处流水线阶段，取值：`pending`、`downloading`、`punctuating`、`resegmenting`、`translating`、`finalizing`、`done`
- `status` 列语义缩小为三个值：`in_progress`、`success`、`failed`
- 旧数据不做迁移，直接重建数据库
- 受影响的 SQL 查询：`_mark_interrupted`、`_find_duplicate`、`_get_active_jobs` 的条件均需适配新语义

### 2. 流水线函数拆分

将 `_execute_pipeline` 拆分为独立函数：

- `_do_download(job_id, url)` — 下载
- `_do_punctuate(job_id, srt_path)` — 标点处理
- `_do_resegment(job_id, srt_path)` — 重新分句
- `_do_translate(job_id, srt_path)` — 翻译
- `_do_finalize(job_id, srt_path)` — 聚合合并 + finalize + 移动文件 + 清理

重试逻辑只重新处理失败 Chunk，然后复用 `_do_finalize`。

### 3. 翻译公共模块

- 废弃 `run_deepseek.py`（子进程入口），抽取 `translate_chunk(chunk_path, output_path)` 公共函数
- `batch_translate.py` 和重试逻辑均直接调用此函数，消除子进程开销和逻辑重复
- `batch_translate.py` 新增 `--skip-translate` 模式，用于仅执行聚合+合并

### 4. API 端点

Worker 新增两个端点：

- `POST /job/<job_id>/retry` — 触发重试，立即返回 200，后台线程执行
- `POST /job/<job_id>/delete` — 删除 DB 行和 `jobs/<job_id>/` 目录，返回操作结果

前端 server 新增转发路由：

- `POST /api/jobs/<job_id>/retry` → worker
- `POST /api/jobs/<job_id>/delete` → worker

并发保护：重试/删除前检查 `status == 'failed'` 并原子更新为 `in_progress`，天然防止重复触发。

### 5. 重试逻辑

1. 校验 `stage = 'translating' AND status = 'failed'`
2. 从 DB 读 progress `A/B`，断言 `A < B`
3. 扫描 chunks 目录得到实际 chunk 总数 `B_actual`，断言 `B == B_actual`
4. 遍历所有 chunk，找出 `chunk_x_chinese.txt` 存在且行数匹配 `chunk_x.txt` 的 Chunk，计数 `A_actual`，断言 `A == A_actual`
5. N = B - A 个失败 Chunk：删除对应的 `_chinese.txt`
6. 串行逐个重译失败 Chunk，每完成一个更新 DB progress
7. 全部成功后调用聚合→合并→finalize→移动文件→清理

### 6. 前端 UI

- 每个失败 Job item 右侧显示操作按钮
- 重试按钮：`stage = 'translating' AND status = 'failed'` 时显示
- 删除按钮：`status = 'failed'`（任意 stage）时显示
- 进行中的 Job 不显示任何按钮
- 按钮通过 `fetch` 发送 POST 请求到对应 API 端点
- Job 列表通过 `_get_active_jobs` 查询 `status IN ('in_progress', 'failed')` 获取
- 前端渲染时使用 `stage`（而非旧的 `status`）映射图标和标签

### 7. 状态图标和标签适配

- `_JOB_ICONS` 和 `_JOB_LABELS` 的键从 `status` 改为 `stage`
- 省略 `done` 和 `failed` 的 stage 映射（前者不展示，后者用 status 判断）

## Testing Decisions

- 测试应关注外部行为：重试后 Job 能从 failed 走到 done，删除后 DB 行和目录消失
- 测试应以 worker 为边界：通过 HTTP API 驱动，检查 DB 状态和文件系统结果
- 无需 mock LLM API，可使用现有 chunk 文件模拟已完成/失败的场景
- 优先测试重试逻辑的校验断言（A≠A_actual 时拒绝）、并发保护（重复点击不重复执行）

## Out of Scope

- 标点处理（punctuating）阶段的重试——该阶段暂时只支持删除
- 下载（downloading）、断句（resegmenting）、收尾（finalizing）阶段的重试
- 重试的并发（并行）翻译——失败 Chunk 串行处理
- 已移到 `video_dir/` 的文件清理——删除操作不回溯清理已成功的产物
- 历史数据迁移——直接重建数据库

## Further Notes

- 真实案例验证：现有一个翻译阶段失败的 Job（`5ef0bd68d027`），11 个 Chunk 中 chunk_001 行数不匹配、chunk_008_chinese.txt 缺失，证明重试逻辑中"文件不存在"和"行数不匹配"两种失败模式需要统一处理。
- `finalize-subtitles.ps1` 在非 debug 模式下自动清理 workspace，重试流程保持一致即可。
- `_mark_interrupted` 在 worker 启动时将所有 `in_progress` 的 Job 标记为 `failed`，需同时设置 `error` 以区分中断原因。
