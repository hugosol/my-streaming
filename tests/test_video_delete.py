"""Tests for video delete endpoint (Slice 01 - Delete Video).

Tests HTTP POST /video/delete endpoint via running a temporary worker server.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib.machinery
import importlib.util

_loader = importlib.machinery.SourceFileLoader("wm", str(Path(__file__).parent.parent / "worker.py"))
_spec = importlib.util.spec_from_loader("wm", _loader)
worker = importlib.util.module_from_spec(_spec)
_loader.exec_module(worker)


def _compute_md5(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()[:8]


def _start_worker(tmpdir: str, port: int):
    """Start worker HTTP server in a background thread with test config."""
    db_path = Path(tmpdir) / "test_jobs.db"
    (Path(tmpdir) / "config.json").write_text(json.dumps({
        "db_path": str(db_path),
        "worker_port": port,
        "video_dir": str(Path(tmpdir) / "videos"),
    }))
    (Path(tmpdir) / "videos").mkdir(parents=True, exist_ok=True)
    worker._conn = None
    worker._config = {}
    worker._CONFIG_PATH = Path(tmpdir) / "config.json"
    worker._ROOT = Path(tmpdir)
    from http.server import HTTPServer
    server = HTTPServer(("127.0.0.1", port), worker.WorkerHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    return server


def _stop_worker(server):
    server.shutdown()
    server.server_close()


def _post(port: int, path: str, data: dict | None = None):
    body = json.dumps(data or {}).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# --- Tracer bullet: delete video with DB record, files, jobs dir, temp dir ---

def test_delete_video_with_db_record():
    tmpdir = tempfile.mkdtemp(prefix="vd_")
    try:
        srv = _start_worker(tmpdir, 19976)
        try:
            # Create a job record directly in DB
            video_name = "test-video.mp4"
            video_md5 = _compute_md5(video_name)
            now = worker._now_iso()
            conn = worker._get_conn()
            conn.execute(
                "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at, video_md5) "
                "VALUES (?, ?, ?, 'done', 'success', '', ?, '', ?, ?, ?)",
                ("job001", "https://youtube.com/watch?v=abc12345678", "abc12345678", video_name, now, now, video_md5),
            )
            conn.commit()

            # Create matching MP4 file
            video_dir = Path(tmpdir) / "videos"
            mp4_path = video_dir / video_name
            mp4_path.write_text("fake-mp4")

            # Create jobs directory
            job_dir = Path(tmpdir) / "jobs" / "job001"
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "somefile.txt").write_text("job-data")

            # Create temp directory
            temp_dir = Path(tmpdir) / "temp" / video_md5
            temp_dir.mkdir(parents=True, exist_ok=True)
            (temp_dir / "segment_001.ts").write_text("fake-ts")

            # POST /video/delete
            st, body = _post(19976, "/video/delete", {"video_md5": video_md5})
            assert st == 200, f"want 200 got {st}: {body}"
            assert body.get("ok") is True, f"want ok: {body}"

            # DB row gone
            row = conn.execute("SELECT * FROM jobs WHERE id = 'job001'").fetchone()
            assert row is None, "job row should be deleted"

            # MP4 file gone
            assert not mp4_path.exists(), "mp4 file should be deleted"

            # Jobs dir gone
            assert not job_dir.exists(), "jobs dir should be deleted"

            # Temp dir gone
            assert not temp_dir.exists(), "temp dir should be deleted"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- Schema migration: video_md5 column added on startup ---

def test_schema_migration_adds_video_md5():
    """Worker startup adds video_md5 column even if DB was created without it."""
    tmpdir = tempfile.mkdtemp(prefix="vm_")
    try:
        db_path = Path(tmpdir) / "test_jobs.db"
        # Pre-create DB without video_md5 column
        import sqlite3
        pre_conn = sqlite3.connect(str(db_path))
        pre_conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                video_id TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'pending',
                status TEXT NOT NULL DEFAULT 'in_progress',
                progress TEXT DEFAULT '',
                video_name TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        pre_conn.commit()
        pre_conn.close()

        srv = _start_worker(tmpdir, 19977)
        try:
            conn = worker._get_conn()
            cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
            assert "video_md5" in cols, f"video_md5 column missing: {cols}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- in_progress jobs protected ---

def test_delete_in_progress_protected():
    tmpdir = tempfile.mkdtemp(prefix="vi_")
    try:
        srv = _start_worker(tmpdir, 19978)
        try:
            video_name = "video-inprog.mp4"
            video_md5 = _compute_md5(video_name)
            now = worker._now_iso()
            conn = worker._get_conn()

            # Create in_progress job
            conn.execute(
                "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at, video_md5) "
                "VALUES (?, ?, ?, 'downloading', 'in_progress', '', ?, '', ?, ?, ?)",
                ("job_ip", "https://youtube.com/watch?v=abc12345678", "abc12345678", video_name, now, now, video_md5),
            )
            # Create failed job with same video_md5
            conn.execute(
                "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at, video_md5) "
                "VALUES (?, ?, ?, 'done', 'failed', '', ?, '', ?, ?, ?)",
                ("job_fail", "https://youtube.com/watch?v=abc12345678", "abc12345678", video_name, now, now, video_md5),
            )
            conn.commit()

            # Create matching MP4
            video_dir = Path(tmpdir) / "videos"
            (video_dir / video_name).write_text("fake")

            st, body = _post(19978, "/video/delete", {"video_md5": video_md5})
            assert st == 200, f"want 200 got {st}: {body}"

            # in_progress job still exists
            row_ip = conn.execute("SELECT * FROM jobs WHERE id = 'job_ip'").fetchone()
            assert row_ip is not None, "in_progress job should NOT be deleted"

            # failed job is gone
            row_fail = conn.execute("SELECT * FROM jobs WHERE id = 'job_fail'").fetchone()
            assert row_fail is None, "failed job should be deleted"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- File-only delete (no DB record) ---

def test_delete_file_only():
    tmpdir = tempfile.mkdtemp(prefix="vf_")
    try:
        srv = _start_worker(tmpdir, 19979)
        try:
            video_name = "manual-video.mp4"
            video_md5 = _compute_md5(video_name)
            video_dir = Path(tmpdir) / "videos"
            mp4_path = video_dir / video_name
            mp4_path.write_text("fake-mp4")

            # Also create a matching SRT
            srt_path = video_dir / "manual-video.srt"
            srt_path.write_text("fake-srt")

            st, body = _post(19979, "/video/delete", {"video_md5": video_md5})
            assert st == 200, f"want 200 got {st}: {body}"
            assert body.get("ok") is True

            assert not mp4_path.exists(), "mp4 file should be deleted"
            assert not srt_path.exists(), "srt file should be deleted"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- No match at all returns 404 ---

def test_delete_no_match():
    tmpdir = tempfile.mkdtemp(prefix="vn_")
    try:
        srv = _start_worker(tmpdir, 19980)
        try:
            st, body = _post(19980, "/video/delete", {"video_md5": "deadbeef"})
            assert st == 404, f"want 404 got {st}: {body}"
            assert "error" in body
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_delete_video_with_db_record,
        test_schema_migration_adds_video_md5,
        test_delete_in_progress_protected,
        test_delete_file_only,
        test_delete_no_match,
    ]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  PASS {name}")
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
