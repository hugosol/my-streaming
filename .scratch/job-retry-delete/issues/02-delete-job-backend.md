# Delete Job Backend

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Add a `POST /job/<job_id>/delete` endpoint to the Worker, and a corresponding `POST /api/jobs/<job_id>/delete` forwarding route on the Server. The endpoint atomically checks that the Job has `status='failed'`, deletes its database row, and removes the `jobs/<job_id>/` directory from disk.

Concurrency protection: before deleting, atomically update `status` to `in_progress` with a `WHERE status = 'failed'` guard. If zero rows are affected (Job already being acted on), return 409 Conflict.

## Acceptance criteria

- [ ] Worker `POST /job/<job_id>/delete` deletes DB row and `jobs/<job_id>/` directory
- [ ] Returns 404 if Job not found
- [ ] Returns 409 if Job `status != 'failed'` (including already-deleting or in_progress)
- [ ] Returns 200 on successful deletion
- [ ] Server `POST /api/jobs/<job_id>/delete` forwards to Worker and relays the response
- [ ] Worker `do_POST` routes paths matching `/job/<hex>/delete` to the handler (path routing works alongside existing `/job` endpoint)

## Blocked by

- `01-db-schema-migration` (needs `stage` column and narrowed `status`)
