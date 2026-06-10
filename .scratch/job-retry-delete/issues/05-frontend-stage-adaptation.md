# Frontend Stage 适配（图标/标签/过滤/隐藏按钮）

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Update the frontend to use the new `stage` field for rendering, and refine which Jobs appear in the task list:

1. **Icons and labels**: `_JOB_ICONS` and `_JOB_LABELS` keys switch from `status` values to `stage` values. The `failed` key is removed from both (failure is a `status`, not a `stage`). The `done` key is omitted (done Jobs won't be rendered in the task list).
2. **Job list filtering**: `_get_active_jobs` (both worker and server) queries `WHERE status IN ('in_progress', 'failed')` — successful Jobs are excluded. The server-side HTML rendering uses `job["stage"]` for icons/labels.
3. **Hide buttons for in_progress**: Jobs with `status='in_progress'` render no action buttons. Only `status='failed'` Jobs show buttons.

This slice does NOT add the retry button — that's Slice 7. It lays the groundwork by fixing icons/labels and button visibility rules.

## Acceptance criteria

- [ ] `_JOB_ICONS` maps `pending`→⏳, `downloading`→⬇, `punctuating`→📝, `resegmenting`→✂, `translating`→🔄, `finalizing`→📦 (no `failed` or `done` keys)
- [ ] `_JOB_LABELS` maps `pending`→等待中, `downloading`→下载中, `punctuating`→标点处理中, `resegmenting`→重新分句中, `translating`→翻译中, `finalizing`→收尾中 (no `failed` or `done` keys)
- [ ] Server `_get_active_jobs` returns only `status IN ('in_progress', 'failed')`
- [ ] Worker `_get_active_jobs` returns only `status IN ('in_progress', 'failed')`
- [ ] Server-side HTML rendering uses `job["stage"]` for icon/label lookup
- [ ] Failed Jobs show a visual indicator (e.g., red border or ❌ prefix) so users can distinguish them from in_progress
- [ ] Jobs with `status='in_progress'` render no action buttons
- [ ] Delete button still works (Slice 3's button renders on `status='failed'` — unchanged)

## Blocked by

- `01-db-schema-migration` (needs `stage` field in query results)
- `03-delete-job-frontend` (builds on the delete button's HTML structure)
