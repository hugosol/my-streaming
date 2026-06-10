# Delete Job Frontend

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Add a delete button to each failed Job in the index page's task list. Clicking it sends a `POST` to `/api/jobs/<job_id>/delete` via `fetch`. The button disables immediately on click to prevent double-submit. On success, the Job item is removed from the DOM. On failure, an error message is shown near the button.

## Acceptance criteria

- [ ] Each Job with `status='failed'` renders a delete button (text: "删除")
- [ ] Clicking delete sends `fetch POST /api/jobs/<job_id>/delete`
- [ ] Button is disabled (`disabled` attribute) immediately on click, before the fetch resolves
- [ ] On success: Job item is removed from the DOM (no page reload needed)
- [ ] On failure: error message is displayed next to the button, button re-enabled
- [ ] Inline `<script>` in `index.html` handles the button events (no new JS file needed — follow existing pattern)
- [ ] Buttons work for Jobs rendered server-side on page load (they appear in the HTML, not injected by JS)

## Blocked by

- `02-delete-job-backend` (needs the API endpoint to exist)
