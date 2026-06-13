Status: ready-for-agent

## Parent

PRD: `.scratch/video-delete-redownload/PRD.md`

## What to build

在视频列表每个视频右侧增加"重新下载"按钮，点击后弹出确认对话框，确认后删除现有文件及数据库记录，使用原始 YouTube 链接自动提交新 Job 重新走完整下载→翻译流水线，然后页面自动刷新。

### 端到端流程

1. 用户浏览首页 → 视频列表每项右侧显示"重新下载"按钮（在"删除"按钮右侧）
2. 点击"重新下载" → 浏览器弹出 `confirm("确定要重新下载视频 <文件名> 吗？这将删除现有文件并重新处理。")`
3. 确认 → 前端发送 `POST /api/videos/<video_md5>/redownload`（`Content-Type: application/json`, body `{}`）
4. Server 将请求代理转发到 Worker `POST /video/redownload`，传参 `{"video_md5": "…"}`
5. Worker 查询原 YouTube URL、清理旧数据、提交新 Job、返回 `{"ok": true}` 或 `{"error": "…"}`
6. 成功 → `location.reload()`；失败 → `alert(error)`

### Worker 端点行为 (`POST /video/redownload`)

接收 `{"video_md5": "abc12345"}`：

1. 用 `video_md5` 查询 DB 中 `status != 'in_progress'` 的记录，按 `created_at DESC` 取最新一条的 `url` 和 `video_id`
2. 若无 DB 记录：返回 404 `{"error": "no video record found for redownload"}`
3. 逐条原子认领（同 Slice 1 模式）：`UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status != 'in_progress'`，检查 `rowcount`
4. 认领成功后删除 DB 行，同时删除 `jobs/<job_id>/` 目录
5. 扫描 `video_dir` 删除匹配的 MP4 及同名 SRT 文件
6. 删除 `temp/<video_md5>/` 转码缓存目录
7. 用步骤 1 获得的 `url` 和 `video_id` 调用 `_create_job(url, video_id)` 创建新 Job
8. 在后台线程调用 `_execute_pipeline(job_id, url)` 启动流水线
9. 成功返回 `{"ok": true}`

> 内部清理逻辑（步骤 3-6）应复用 Slice 1 中的清理函数，避免重复实现。

### Server 转发路由

- 新增路由正则 `^/api/videos/([a-f0-9]+)/redownload$`，匹配 `POST` 方法
- 在 `_forward_video_action` 中增加 `"redownload"` 分支（该函数在 Slice 1 中创建）
- 代理转发到 Worker `POST /video/redownload`，body 为 `{"video_md5": "<video_md5>"}`

### 前端变更

**模板：** `_INDEX_ITEM` 常量在 Slice 1 基础上追加"重新下载"按钮：
```
<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}<button class="job-btn video-delete-btn" data-video-md5="{{id}}">删除</button><button class="job-btn video-redownload-btn" data-video-md5="{{id}}">重新下载</button></div>
```

**JS（event delegation）：**
```js
document.addEventListener('click', async function(e) {
    const btn = e.target.closest('.video-redownload-btn');
    if (!btn || btn.disabled) return;

    const videoMd5 = btn.dataset.videoMd5;
    const videoName = btn.parentElement.querySelector('a').textContent;
    if (!confirm('确定要重新下载视频 ' + videoName + ' 吗？这将删除现有文件并重新处理。')) return;

    btn.disabled = true;
    btn.textContent = '...';
    try {
        const resp = await fetch('/api/videos/' + videoMd5 + '/redownload', { method: 'POST' });
        const data = await resp.json();
        if (resp.ok) {
            location.reload();
        } else {
            alert(data.error || '重新下载失败');
            btn.disabled = false;
            btn.textContent = '重新下载';
        }
    } catch (err) {
        alert('服务器错误: ' + err.message);
        btn.disabled = false;
        btn.textContent = '重新下载';
    }
});
```

## Acceptance criteria

- [ ] 有 DB 记录的视频：旧 DB 行删除、旧文件清理、新 Job 创建并入队、流水线启动
- [ ] 无 DB 记录的视频：返回 404 `{"error": "no video record found for redownload"}`
- [ ] `in_progress` 状态的 Job 记录不被删除
- [ ] 多条 Job 记录匹配同一 `video_md5` 时，取最新记录的 `url`
- [ ] 并发保护：同时发起多个红色ownload 请求，只有一个成功执行
- [ ] 新 Job 的 `url` 字段与原始 Job 一致
- [ ] 新 Job 的 `video_id` 字段与原始 Job 一致
- [ ] 清理逻辑复用 Slice 1 中的内部函数（不重复实现）
- [ ] 前端点击红色ownload 按钮弹出确认对话框，确认后按钮禁用，成功后页面刷新，失败后按钮恢复并 alert
- [ ] 按钮样式与现有 Job 操作按钮一致（复用 `.job-btn` CSS 类）

## Blocked by

- `01-delete-video`（依赖 video_md5 列、`_do_download` 写入逻辑、清理函数）
