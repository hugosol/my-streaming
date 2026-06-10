# DB Schema Migration — Add `stage` Column

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Add a `stage` column to the `jobs` table so the system can distinguish "which pipeline stage a Job is in" from "whether the Job succeeded or failed". Narrow `status` to exactly three values: `in_progress`, `success`, `failed`.

This is a breaking schema change — no old-data migration, just rebuild the DB. All SQL queries that touch `status` or select columns from `jobs` must be updated to the new semantics.

## Acceptance criteria

- [ ] `jobs` table has a `stage` column with values: `pending`, `downloading`, `punctuating`, `resegmenting`, `translating`, `finalizing`, `done`
- [ ] `status` column only ever contains `in_progress`, `success`, or `failed`
- [ ] `_create_job` sets `stage='pending'` and `status='in_progress'`
- [ ] `_mark_interrupted` sets `status='failed'`, `stage` untouched, and sets `error` to distinguish interrupted Jobs from pipeline-failed Jobs
- [ ] `_find_duplicate` filters on `status != 'success'` (not `status NOT IN ('done', 'failed')`)
- [ ] Worker `_get_active_jobs` returns `status IN ('in_progress', 'failed')` and includes `stage` in SELECT
- [ ] Server `_get_active_jobs` returns `status IN ('in_progress', 'failed')` and includes `stage` in SELECT
- [ ] `_get_job` still returns all columns (including the new `stage`)

## Blocked by

None — can start immediately.
