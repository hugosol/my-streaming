"""Tests for DB schema migration (Slice 01).

Verifies: stage column, narrowed status, _create_job semantics,
_mark_interrupted behavior, _find_duplicate filter.
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
import importlib.machinery
import importlib.util

# Load worker.py as a module (worker/ package shadows the file)
_loader = importlib.machinery.SourceFileLoader("worker_mod", str(Path(__file__).parent.parent / "worker.py"))
_spec = importlib.util.spec_from_loader("worker_mod", _loader)
worker = importlib.util.module_from_spec(_spec)
_loader.exec_module(worker)

def _setup_test_db():
    """Create a fresh test DB and point worker at it."""
    tmpdir = tempfile.mkdtemp(prefix="test_db_")
    db_path = Path(tmpdir) / "test_jobs.db"

    # Patch worker globals to use test DB
    worker._conn = None
    worker._db_path = lambda: db_path

    # Write a minimal config so _load_config works
    config_path = Path(tmpdir) / "config.json"
    config_path.write_text(json.dumps({"db_path": str(db_path)}))
    worker._CONFIG_PATH = config_path
    worker._config = {}

    # Also need _ROOT-relative paths to work
    # _create_job doesn't need _ROOT, just the DB
    return tmpdir, db_path


def _teardown_test_db(tmpdir: str):
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)
    worker._conn = None
    worker._config = {}
    # Restore original config path
    worker._CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def test_schema_has_stage_column():
    """New DB table includes 'stage' column with default 'pending'."""
    tmpdir, db_path = _setup_test_db()
    try:
        # Force connection creation (triggers CREATE TABLE)
        conn = worker._get_conn()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "stage" in cols, f"stage column missing, got: {cols}"
    finally:
        _teardown_test_db(tmpdir)


def test_create_job_sets_stage_and_status():
    """_create_job sets stage='pending' and status='in_progress'."""
    tmpdir, db_path = _setup_test_db()
    try:
        job_id = worker._create_job("https://youtube.com/watch?v=test123", "test123")
        job = worker._get_job(job_id)
        assert job is not None
        assert job["stage"] == "pending", f"stage={job['stage']}"
        assert job["status"] == "in_progress", f"status={job['status']}"
    finally:
        _teardown_test_db(tmpdir)


def test_find_duplicate_only_in_progress():
    """_find_duplicate only matches jobs with status='in_progress'."""
    tmpdir, db_path = _setup_test_db()
    try:
        # Create a job
        job_id = worker._create_job("https://youtube.com/watch?v=dup1", "dup1")
        # It should be found as duplicate (status=in_progress)
        dup = worker._find_duplicate("dup1")
        assert dup is not None, "Should find in_progress duplicate"
        assert dup["id"] == job_id

        # Manually set this job to failed
        worker._update_job(job_id, status="failed", stage="downloading")
        dup2 = worker._find_duplicate("dup1")
        assert dup2 is None, "Should NOT find failed job as duplicate"

        # Manually set to success
        worker._update_job(job_id, status="success", stage="done")
        dup3 = worker._find_duplicate("dup1")
        assert dup3 is None, "Should NOT find success job as duplicate"
    finally:
        _teardown_test_db(tmpdir)


def test_mark_interrupted_only_in_progress():
    """_mark_interrupted marks only in_progress jobs as failed, sets error."""
    tmpdir, db_path = _setup_test_db()
    try:
        # Create three jobs with different statuses
        j1 = worker._create_job("https://youtube.com/watch?v=int1", "int1")
        j2 = worker._create_job("https://youtube.com/watch?v=int2", "int2")
        # Leave j1 as in_progress, mark j2 as done then failed
        worker._update_job(j2, status="success", stage="done")

        j3 = worker._create_job("https://youtube.com/watch?v=int3", "int3")
        worker._update_job(j3, status="failed", stage="translating", error="翻译失败")

        # Mark interrupted
        worker._mark_interrupted()

        j1_after = worker._get_job(j1)
        j2_after = worker._get_job(j2)
        j3_after = worker._get_job(j3)

        # j1 was in_progress → should be failed with Worker restarted error
        assert j1_after["status"] == "failed", f"j1 status={j1_after['status']}"
        assert j1_after["error"] == "Worker restarted", f"j1 error={j1_after['error']}"
        # j1 stage should be untouched (was 'pending')
        assert j1_after["stage"] == "pending", f"j1 stage={j1_after['stage']}"

        # j2 was success → should remain success
        assert j2_after["status"] == "success", f"j2 status={j2_after['status']}"

        # j3 was already failed → should remain failed, original error preserved
        assert j3_after["status"] == "failed", f"j3 status={j3_after['status']}"
        assert j3_after["error"] == "翻译失败", f"j3 error={j3_after['error']}"
    finally:
        _teardown_test_db(tmpdir)


if __name__ == "__main__":
    tests = [
        test_schema_has_stage_column,
        test_create_job_sets_stage_and_status,
        test_find_duplicate_only_in_progress,
        test_mark_interrupted_only_in_progress,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as e:
            print(f"FAIL {test.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR {test.__name__}: {e}")
            failures += 1
    print()
    if failures:
        print(f"{failures}/{len(tests)} FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed")
