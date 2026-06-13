Status: ready-for-agent

## Parent

PRD: `.scratch/video-delete-redownload/PRD.md`

## What to build

在视频列表每个视频右侧增加"删除"按钮，点击后弹出确认对话框，确认后删除视频及其所有关联数据（数据库记录、视频文件、字幕文件、Job 中间产物目录、HLS 转码缓存），然后页面自动刷新。

**本 Slice 同时铺设 video_md5 基础设施**（DB schema 迁移 + 下载时写入），供 Slice 2 复用。

### 端到端流程

1. 用户浏览首页 → 视频列表每项右侧显示"删除"按钮
2. 点击"删除" → 浏览器弹出 `confirm("确定要删除视频 <文件名> 及其所有关联数据吗？")`
3. 确认 → 前端发送 `POST /api/videos/<video_md5>/delete`（`Content-Type: application/json`, body `{}`）
4. Server 将请求代理转发到 Worker `POST /video/delete`，传参 `{"video_md5": "…"}`
5. Worker 执行清理并返回 `{"ok": true}` 或 `{"error": "…"}`
6. 成功 → `location.reload()`；失败 → `alert(error)`

### Worker 端点行为 (`POST /video/delete`)

接收 `{"video_md5": "abc12345"}`：

1. 用 `video_md5` 查询 DB 中 `status != 'in_progress'` 的 Job 记录
2. 逐条原子认领：`UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status != 'in_progress'`，检查 `rowcount` 防止并发重复删除
3. 认领成功后删除 DB 行，同时删除 `jobs/<job_id>/` 目录
4. 用 `video_md5` 扫描 `video_dir`，找到匹配的 MP4 文件及同名 SRT 文件并删除
5. 删除 `temp/<video_md5>/` 转码缓存目录
6. 若无 DB 记录：降级扫描 `video_dir` 直接删文件（支持手动复制进目录的视频）
7. 完全无匹配返回 404
8. 成功返回 `{"ok": true}`

### DB Schema 变更

- `jobs` 表新增 `video_md5 TEXT DEFAULT ''` 列，存储视频文件名的 MD5 前 8 位十六进制
- Worker 启动时检测列是否存在，不存在则 `ALTER TABLE jobs ADD COLUMN video_md5 TEXT DEFAULT ''`
- `_do_download` 下载完成后从 `Path.name`（含 `.mp4` 扩展名）计算 MD5 前 8 位，写入 `video_md5`
- `video_md5` 与前端 `scanner.py` 的 Video ID 计算方式一致（同为 `hashlib.md5(filename.encode()).hexdigest()[:8]`）

### Server 转发路由

- 新增路由正则 `^/api/videos/([a-f0-9]+)/delete$`，匹配 `POST` 方法
- 实现 `_forward_video_action(video_md5, "delete")`，复用 `_forward_job_action` 代理模式：
  - 向 `http://127.0.0.1:{worker_port}/video/delete` 发送 POST
  - body 为 `{"video_md5": "<video_md5>"}`
  - 将 Worker 响应原样返回给前端

### 前端变更

**模板：** `_INDEX_ITEM` 常量从
```
<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}</div>
```
扩展为（含删除按钮）：
```
<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}<button class="job-btn video-delete-btn" data-video-md5="{{id}}">删除</button></div>
```

**JS（event delegation）：**
```js
document.addEventListener('click', async function(e) {
    const btn = e.target.closest('.video-delete-btn');
    if (!btn || btn.disabled) return;

    const videoMd5 = btn.dataset.videoMd5;
    const videoName = btn.parentElement.querySelector('a').textContent;
    if (!confirm('确定要删除视频 ' + videoName + ' 及其所有关联数据吗？')) return;

    btn.disabled = true;
    btn.textContent = '...';
    try {
        const resp = await fetch('/api/videos/' + videoMd5 + '/delete', { method: 'POST' });
        const data = await resp.json();
        if (resp.ok) {
            location.reload();
        } else {
            alert(data.error || '删除失败');
            btn.disabled = false;
            btn.textContent = '删除';
        }
    } catch (err) {
        alert('服务器错误: ' + err.message);
        btn.disabled = false;
        btn.textContent = '删除';
    }
});
```

## Acceptance criteria

- [ ] Worker 启动时自动迁移 `video_md5` 列（已有 DB 无列时 ALTER TABLE ADD COLUMN）
- [ ] 视频下载完成后 `video_md5` 列被正确写入（值为文件名 MD5 前 8 位）
- [ ] 删除有 DB 记录的视频：DB 行消失、`jobs/<id>/` 目录消失、`video_dir` 中 MP4/SRT 消失、`temp/<md5>/` 消失
- [ ] `in_progress` 状态的 Job 记录不被删除
- [ ] 多条 Job 记录匹配同一 `video_md5` 时，逐条认领删除，未认领成功的跳过
- [ ] 无 DB 记录的视频：仅删除 `video_dir` 中的文件
- [ ] 完全无匹配（既无 DB 也无文件）返回 404 `{"error": "no video found"}`
- [ ] 并发保护：同时发起多个删除请求，只有一个成功执行清理
- [ ] 前端点击删除按钮弹出确认对话框，确认后按钮禁用，成功后页面刷新，失败后按钮恢复并 alert
- [ ] 按钮样式与现有 Job 操作按钮一致（复用 `.job-btn` CSS 类）

## Blocked by

None — can start immediately.
