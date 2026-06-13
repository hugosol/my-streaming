"""Tests for video redownload endpoint (Slice 02 - Redownload Video).

Tests HTTP POST /video/redownload endpoint via running a temporary worker server.
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


# --- Tracer bullet: redownload creates new job, cleans old data ---

def test_redownload_creates_new_job():
    tmpdir = tempfile.mkdtemp(prefix="vr_")
    try:
        srv = _start_worker(tmpdir, 19986)
        try:
            video_name = "test-redl.mp4"
            video_md5 = _compute_md5(video_name)
            original_url = "https://youtube.com/watch?v=xyz98765432"
            original_video_id = "xyz98765432"
            now = worker._now_iso()
            conn = worker._get_conn()

            # Create old job record
            conn.execute(
                "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at, video_md5) "
                "VALUES (?, ?, ?, 'done', 'success', '', ?, '', ?, ?, ?)",
                ("job_old", original_url, original_video_id, video_name, now, now, video_md5),
            )
            conn.commit()

            # Create matching MP4
            video_dir = Path(tmpdir) / "videos"
            mp4_path = video_dir / video_name
            mp4_path.write_text("fake-mp4")

            # Create jobs directory
            job_dir = Path(tmpdir) / "jobs" / "job_old"
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "data.txt").write_text("old-job")

            # Create temp directory
            temp_dir = Path(tmpdir) / "temp" / video_md5
            temp_dir.mkdir(parents=True, exist_ok=True)
            (temp_dir / "seg.ts").write_text("old-ts")

            # POST /video/redownload
            st, body = _post(19986, "/video/redownload", {"video_md5": video_md5})
            assert st == 200, f"want 200 got {st}: {body}"
            assert body.get("ok") is True, f"want ok: {body}"

            # Old DB row gone
            old_row = conn.execute("SELECT * FROM jobs WHERE id = 'job_old'").fetchone()
            assert old_row is None, "old job row should be deleted"

            # Old files gone
            assert not mp4_path.exists(), "old mp4 should be deleted"
            assert not job_dir.exists(), "old jobs dir should be deleted"
            assert not temp_dir.exists(), "old temp dir should be deleted"

            # New job created
            new_rows = conn.execute(
                "SELECT * FROM jobs WHERE url = ? AND status = 'in_progress'",
                (original_url,),
            ).fetchall()
            assert len(new_rows) >= 1, "new job should be created with same URL"

            cols = [desc[0] for desc in conn.execute("SELECT * FROM jobs LIMIT 0").description]
            new_job = dict(zip(cols, new_rows[0]))
            assert new_job["video_id"] == original_video_id, f"video_id mismatch: {new_job['video_id']}"
            assert new_job["status"] == "in_progress", f"status should be in_progress: {new_job['status']}"
            assert new_job["url"] == original_url, f"url mismatch: {new_job['url']}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- No DB record returns 404 ---

def test_redownload_no_db_record():
    tmpdir = tempfile.mkdtemp(prefix="vrn_")
    try:
        srv = _start_worker(tmpdir, 19987)
        try:
            st, body = _post(19987, "/video/redownload", {"video_md5": "deadbeef"})
            assert st == 404, f"want 404 got {st}: {body}"
            assert "error" in body
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- in_progress jobs protected ---

def test_redownload_in_progress_protected():
    tmpdir = tempfile.mkdtemp(prefix="vri_")
    try:
        srv = _start_worker(tmpdir, 19988)
        try:
            video_name = "video-rip.mp4"
            video_md5 = _compute_md5(video_name)
            original_url = "https://youtube.com/watch?v=abc11111111"
            original_video_id = "abc11111111"
            now = worker._now_iso()
            conn = worker._get_conn()

            # Create in_progress job (should NOT be touched)
            conn.execute(
                "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at, video_md5) "
                "VALUES (?, ?, ?, 'downloading', 'in_progress', '', ?, '', ?, ?, ?)",
                ("job_ip", original_url, original_video_id, video_name, now, now, video_md5),
            )
            conn.commit()

            # No failed job — so redownload should find in_progress only and return 404
            st, body = _post(19988, "/video/redownload", {"video_md5": video_md5})
            assert st == 404, f"want 404 (only in_progress) got {st}: {body}"

            # in_progress job still exists
            row_ip = conn.execute("SELECT * FROM jobs WHERE id = 'job_ip'").fetchone()
            assert row_ip is not None, "in_progress job should NOT be deleted"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_redownload_creates_new_job,
        test_redownload_no_db_record,
        test_redownload_in_progress_protected,
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
