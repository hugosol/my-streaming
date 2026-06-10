# Retry Job Backend

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Add a `POST /job/<job_id>/retry` endpoint to the Worker, and a corresponding `POST /api/jobs/<job_id>/retry` forwarding route on the Server. The endpoint re-runs only the failed chunks of a translation-stage Job, then re-finalizes.

**Retry logic:**

1. Validate `stage = 'translating' AND status = 'failed'`; reject otherwise (400)
2. Atomically set `status = 'in_progress'` with `WHERE status = 'failed'` guard; 409 if already being retried
3. Read progress `A/B` from DB; assert `A < B`
4. Scan workspace chunks directory; count chunk files → `B_actual`; assert `B == B_actual`
5. For each chunk (`chunk_NNN.txt`), check `chunk_NNN_chinese.txt` exists AND its line count matches `chunk_NNN.txt` line count; count successes → `A_actual`; assert `A == A_actual`
6. Delete `_chinese.txt` files for the `N = B - A` failed chunks
7. Serially retranslate each failed chunk using `translate_chunk()`; after each success, increment progress in DB (`(A+1)/B`, `(A+2)/B`, ...)
8. After all chunks succeed, call `_do_finalize(job_id, srt_path)` to aggregate → combine → finalize → move files → clean up

**Progress tracking**: During retry, the DB `progress` field shows `A/B` advancing as each chunk completes, so the frontend can poll and display real-time progress.

**Multiple retries**: If retry fails again (a chunk fails), the Job returns to `status='failed'` with `error` set. The user can retry again — the logic re-validates and picks up from the surviving `_chinese.txt` files.

## Acceptance criteria

- [ ] Worker `POST /job/<job_id>/retry` starts retry in a background thread, returns 200 immediately with `{"job_id": "...", "status": "retrying"}`
- [ ] Returns 400 if Job `stage != 'translating'` or `status != 'failed'`
- [ ] Returns 404 if Job not found
- [ ] Returns 409 if Job is already being retried (concurrency guard)
- [ ] Server `POST /api/jobs/<job_id>/retry` forwards to Worker and relays the response
- [ ] Retry correctly identifies failed chunks by: missing `_chinese.txt`, or line-count mismatch between chunk and its `_chinese.txt`
- [ ] Retry refuses to start if `A >= B` or `A != A_actual` or `B != B_actual` (assertions fail → set `error`, return Job to `failed`)
- [ ] After successful retry, Job reaches `stage='done', status='success'` and the bilingual SRT + MP4 appear in `video_dir/`
- [ ] `jobs/<job_id>/` directory is cleaned up after successful retry (same as normal pipeline)
- [ ] Progress updates are written to DB after each chunk completes during retry
- [ ] If retry fails mid-way, remaining failed chunks cause `status='failed'` with `error` set; user can retry again

## Blocked by

- `04-pipeline-translation-refactor` (needs `_do_finalize`, `translate_chunk()`, and `--skip-translate`)
