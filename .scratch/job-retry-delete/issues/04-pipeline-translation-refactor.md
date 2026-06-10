# Pipeline + Translation Module 模块化重构

**Status:** `ready-for-agent`

## Parent

`.scratch/job-retry-delete/PRD.md`

## What to build

Two tightly-related refactors that together make the pipeline callable in pieces:

**A. Split the monolithic `_execute_pipeline`** into five stage functions:
- `_do_download(job_id, url)` — download SRT + MP4, set `stage='downloading'`
- `_do_punctuate(job_id, srt_path)` — punctuation detection and DeepSeek chunk processing, set `stage='punctuating'`
- `_do_resegment(job_id, srt_path)` — re-segmentation, set `stage='resegmenting'`
- `_do_translate(job_id, srt_path)` — invoke `batch_translate.py`, set `stage='translating'`
- `_do_finalize(job_id, srt_path)` — aggregate + combine + finalize + move files + clean up, set `stage='finalizing'` then `stage='done'`

Each function receives only what it needs. `_execute_pipeline` becomes a thin orchestrator that calls them in sequence and handles the top-level try/except. On failure at any stage, set `status='failed'` with appropriate `error` — the `stage` field retains the stage where it failed.

**B. Extract `translate_chunk()` and add `--skip-translate`:**
- Extract the core "send chunk to DeepSeek API" logic from `run_deepseek.py` into a plain function `translate_chunk(chunk_path, output_path) -> bool` in a shared module (e.g., `worker/translate.py`)
- `batch_translate.py` imports and calls `translate_chunk()` directly — no subprocess, no `run_deepseek.py`
- Remove `run_deepseek.py`
- Add `--skip-translate` flag to `batch_translate.py`: when set, skip chunk translation entirely and only run aggregate + combine (used by retry when all chunks are already done)

## Acceptance criteria

- [ ] Full pipeline (download → punctuate → resegment → translate → finalize) still completes successfully end-to-end
- [ ] Each stage function sets `stage` before work begins; on failure sets `status='failed'` with `error`; on success advances to the next stage
- [ ] `_do_finalize` sets `stage='done'` and `status='success'` on completion
- [ ] `translate_chunk()` can be imported and called directly from `batch_translate.py`
- [ ] `run_deepseek.py` is deleted
- [ ] `batch_translate.py --skip-translate` skips all chunk processing, reads existing `_chinese.txt` files, aggregates them into the combined Chinese text, and runs the combine step — exiting 0 on success
- [ ] Existing `test_worker.py` still passes

## Blocked by

- `01-db-schema-migration` (needs `stage` column to exist)
