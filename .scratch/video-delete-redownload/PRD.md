# PRD: 视频删除与重新下载

**Status:** `ready-for-agent`

## Problem Statement

当前视频列表仅展示视频名称和播放链接，用户无法直接在界面上管理视频文件。如果一个视频下载失败、文件损坏、或用户想更换字幕配置重处理，只能手动到 `video_dir` 目录删文件，再回到前端重新粘贴 YouTube 链接提交。操作繁琐、容易出错。

## Solution

在视频列表每个视频右侧增加两个操作按钮：

1. **删除**：弹出确认对话框，确认后搜索数据库中同名记录并删除（跳过进行中的），同时删除 `video_dir` 中的视频和字幕文件、`jobs/` 中间产物目录、`temp/` 转码缓存。
2. **重新下载**：弹出确认对话框，确认后搜索数据库获取原始 YouTube 链接，删除现有文件后重新触发完整下载→翻译流水线。

## User Stories

1. 作为用户，我希望能从视频列表中直接删除一个视频及其所有关联数据，无需手动到文件系统中操作。
2. 作为用户，我希望能重新下载一个视频（重新走完整流水线），以便在字幕配置变更后重处理。
3. 作为用户，我希望能看到确认对话框再执行删除，避免误操作。
4. 作为用户，我希望能看到确认对话框再执行重新下载，因为这会删除现有文件。
5. 作为用户，我希望删除操作能同时清理数据库中已终结的 Job 记录。
6. 作为用户，我希望删除操作不删除正在进行的（`in_progress`）Job 记录，避免干扰正在运行的流水线。
7. 作为用户，我希望删除操作也清理 `video_dir` 中的字幕文件（SRT 等同名文件）。
8. 作为用户，我希望删除操作也清理 HLS 转码临时分段（`temp/` 目录）。
9. 作为用户，我希望重新下载时使用原始 YouTube 链接自动提交新 Job，无需手动查找和粘贴。
10. 作为用户，我希望能删除手动复制到 `video_dir` 的视频（数据库中无记录），此时仅删文件。
11. 作为用户，我希望对无数据库记录的视频点"重新下载"时收到明确报错。
12. 作为用户，我希望操作失败时能看到明确的错误提示。
13. 作为用户，我希望操作成功后页面自动刷新，看到最新的视频列表和任务列表。
14. 作为用户，我希望按钮样式和现有 Job 操作按钮保持一致。

## Implementation Decisions

### 1. 数据库 Schema 变更

- `jobs` 表新增 `video_md5` 列（TEXT DEFAULT ''），存储视频文件名的 MD5 前 8 位十六进制。
- 该值在下载完成后由 `_do_download` 写入，与前端扫描计算的 Video ID 一致。
- 已有数据库通过启动时的列检测自动 `ALTER TABLE ADD COLUMN`。

### 2. Video ID 体系

- 前端 Video ID：`MD5(完整文件名)[:8]`，扫描 `video_dir` 时动态计算，用于 `/play/<id>` 路由。
- DB `video_md5`：与前端 Video ID 同源（同为文件名 MD5 前 8 位），作为视频查找的精确匹配键。
- DB `video_id`：保持不变，仍为 YouTube 视频 ID（11 位），用于去重。
- 前端将 `video_md5` 随按钮请求发送给后端，不再使用 `video_name` 模糊匹配。

### 3. Worker API 端点

Worker 新增两个端点，传参 `{"video_md5": "abc12345"}`：

- **`POST /video/delete`**：删除视频及其所有关联数据。处理流程：
  1. 用 `video_md5` 查 DB，筛选 `status != 'in_progress'` 的记录
  2. 原子认领（`UPDATE ... WHERE status != 'in_progress'`）后删除 DB 行
  3. 删除 `jobs/<job_id>/` 目录
  4. 用 `video_md5` 扫描 `video_dir` 找到匹配文件，删除 MP4 及同名 SRT 文件
  5. 删除 `temp/<video_md5>/` 转码缓存
  6. 若无 DB 记录，降级扫描 `video_dir` 直接删文件（支持手动复制进目录的视频）
  7. 完全无匹配返回 404

- **`POST /video/redownload`**：删除现有文件后重新触发完整流水线。处理流程：
  1. 用 `video_md5` 查 DB，筛选 `status != 'in_progress'` 的记录
  2. 取最新记录的 `url` 字段
  3. 原子认领后删除 DB 行
  4. 删除 `jobs/<job_id>/` 目录
  5. 扫描 `video_dir` 删除匹配文件
  6. 删除 `temp/<video_md5>/` 转码缓存
  7. 用 `url` 提交新 Job（复用现有 `_create_job` + `_execute_pipeline`）
  8. 若无 DB 记录，返回 404 报错

- 响应格式：成功返回 `{"ok": true}`，失败返回 `{"error": "..."}` 及对应 HTTP 状态码。

### 4. 并发保护

- 删除/重新下载前用 `UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status != 'in_progress'` 原子认领，检查 `rowcount` 防止重复执行。
- 同一 `video_md5` 匹配多条记录时，逐条认领删除，未认领成功的（已被并发抢走或状态不符）跳过。

### 5. 前端 UI

- 按钮位于视频列表项最右侧：`[视频名] [CC] [删除] [重新下载]`
- 按钮复用 `.job-btn` CSS 类，文字为"删除"和"重新下载"
- 使用浏览器原生 `confirm()` 弹出确认对话框：
  - 删除："确定要删除视频 `<文件名>` 及其所有关联数据吗？"
  - 重新下载："确定要重新下载视频 `<文件名>` 吗？这将删除现有文件并重新处理。"
- 操作失败使用 `alert()` 显示错误信息
- 操作成功后执行 `location.reload()` 整页刷新

### 6. 前端 JS 交互

- 使用 event delegation 模式监听 `.video-delete-btn` 和 `.video-redownload-btn` 的点击
- 按钮通过 `data-video-md5` 属性传递视频标识
- 发送 `fetch` POST 请求到 server API 端点
- 点击后按钮禁用，完成后恢复（防止连点）

### 7. Server 转发路由

Server 新增转发路由，将前端请求代理到 Worker：

- `POST /api/videos/<video_md5>/delete` → Worker `POST /video/delete`
- `POST /api/videos/<video_md5>/redownload` → Worker `POST /video/redownload`

复用现有的 `_forward_job_action` 模式。

### 8. 重新下载的 Job 提交

重新下载提交新 Job 时直接调用 `_create_job(url, video_id)` 和后台线程 `_execute_pipeline`，不必经过 Worker 自身的 HTTP 端点，避免额外的网络跳转。

## Testing Decisions

- 测试以 Worker HTTP 端点为边界，通过 HTTP 请求驱动，验证响应码、DB 状态和文件系统结果。
- 测试应关注外部行为：删除后 DB 行和文件消失，重新下载后新 Job 入队且旧文件清除。
- 优先测试的边界场景：有匹配记录、无匹配记录、含 `in_progress` 记录、重复请求的并发保护。
- 前端 JS 不纳入自动化测试，沿用现有惯例。
- 参考现有测试 `test_delete_job.py`、`test_retry_job.py` 的测试框架和辅助函数。

## Out Of Scope

- 视频文件重命名的支持——文件名变化后 `video_md5` 也变化，视为不同视频。
- 重新下载时的重试机制——和创建新 Job 一致，由现有流水线自行处理。
- 下载或处理中视频的删除——`in_progress` 记录被保护，不删除。
- 视频删除后正在播放的 HLS 流中断问题——直接删除不做额外处理。
- 按钮的 loading 动画或 toast 通知——使用原生 `confirm()` 和 `alert()`。

## Further Notes

- `video_md5` 由文件名（含 `.mp4` 扩展名）的 MD5 计算，与现有 `scanner.py` 的 Video ID 计算方式一致。
- `_do_download` 已有 `video_name` 写入逻辑（使用 `Path.stem` 不含扩展名），`video_md5` 写入时需从 `Path.name`（含扩展名）计算。
- Worker 降级扫描时需知道 `video_dir` 路径，该路径已通过 `_load_config()` 获取。
- Server 进程和 Worker 进程各自持有独立的 DB 连接，Server 只读、Worker 读写，此架构不变。
