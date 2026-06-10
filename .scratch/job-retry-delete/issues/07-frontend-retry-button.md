# Frontend Retry 按钮

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Add a retry button next to the delete button on failed translation-stage Jobs. The button sends a `POST` to `/api/jobs/<job_id>/retry` via `fetch`. Like the delete button, it disables on click to prevent double-submit.

**Visibility rules** (building on Slice 5's foundation):
- Retry button: visible when `stage='translating' AND status='failed'`
- Delete button: visible when `status='failed'` (any stage)
- No buttons: when `status='in_progress'`

When retry starts, the Job transitions to `status='in_progress'`, which means both buttons should disappear on the next poll (the Job item now shows a progress indicator instead). The existing polling logic in the frontend already refreshes periodically.

## Acceptance criteria

- [ ] Retry button (text: "重试") appears only on Jobs where `stage='translating' AND status='failed'`
- [ ] Delete button appears on all Jobs where `status='failed'` (any stage)
- [ ] Retry button disables on click, sends `fetch POST /api/jobs/<job_id>/retry`
- [ ] On success response: button area shows "重试中..." briefly, then buttons disappear when polling picks up `status='in_progress'`
- [ ] On failure response: error message shown, button re-enabled
- [ ] A Job that is `stage='downloading' AND status='failed'` shows only the delete button (no retry button)
- [ ] A Job that is `stage='finalizing' AND status='failed'` shows only the delete button (no retry button)

## Blocked by

- `05-frontend-stage-adaptation` (needs stage-based icon/label/visibility logic)
- `06-retry-job-backend` (needs the retry API endpoint to exist)
