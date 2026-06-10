"""Tests for retry job endpoint (Slice 06).

Verifies retry validation, concurrency protection, and endpoint routing.
Does NOT test full translation (requires API keys).
"""

import json
import os
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


def _start_worker(tmpdir: str, port: int):
    db_path = Path(tmpdir) / "test_jobs.db"
    (Path(tmpdir) / "config.json").write_text(json.dumps({
        "db_path": str(db_path),
        "worker_port": port,
        "video_dir": tmpdir,
    }))
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


def test_retry_nonexistent_job():
    tmpdir = tempfile.mkdtemp(prefix="tr_")
    try:
        srv = _start_worker(tmpdir, 19886)
        try:
            st, body = _post(19886, "/job/deadbeef0000/retry")
            assert st == 404, f"want 404 got {st}: {body}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_retry_wrong_stage():
    """Retry on non-translating stage returns 400."""
    tmpdir = tempfile.mkdtemp(prefix="tr_")
    try:
        srv = _start_worker(tmpdir, 19887)
        try:
            st, body = _post(19887, "/job", {"url": "https://youtube.com/watch?v=rtrytest000"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]
            # Set to failed at downloading stage (not translating)
            worker._update_job(jid, status="failed", stage="downloading", error="e")
            st, body = _post(19887, f"/job/{jid}/retry")
            assert st == 400, f"want 400 got {st}: {body}"
            assert "translating" in body.get("error", "").lower()
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_retry_in_progress_rejected():
    """Retry on in_progress job returns 409."""
    tmpdir = tempfile.mkdtemp(prefix="tr_")
    try:
        srv = _start_worker(tmpdir, 19888)
        try:
            jid = worker._create_job("https://youtube.com/watch?v=rtrytest001", "rtrytest001")
            worker._update_job(jid, stage="translating")  # status stays in_progress
            st, body = _post(19888, f"/job/{jid}/retry")
            assert st == 409, f"want 409 got {st}: {body}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def test_retry_validation_progress_broken():
    """Retry with A >= B returns 400 (bad progress)."""
    tmpdir = tempfile.mkdtemp(prefix="tr_")
    try:
        srv = _start_worker(tmpdir, 19889)
        try:
            st, body = _post(19889, "/job", {"url": "https://youtube.com/watch?v=rtrytest002"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]
            worker._update_job(jid, status="failed", stage="translating",
                               progress="5/3", error="e")
            st, body = _post(19889, f"/job/{jid}/retry")
            assert st == 400, f"want 400 got {st}: {body}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_retry_starts_background():
    """Valid retry returns 200 and starts in background."""
    tmpdir = tempfile.mkdtemp(prefix="tr_")
    try:
        srv = _start_worker(tmpdir, 19890)
        try:
            st, body = _post(19890, "/job", {"url": "https://youtube.com/watch?v=rtrytest003"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]

            # Set up a valid retry state
            job_dir = Path(tmpdir) / "jobs" / jid
            srt_path = job_dir / "test.srt"
            srt_path.parent.mkdir(parents=True, exist_ok=True)
            srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello.\n\n", encoding="utf-8")

            workspace_dir = job_dir / f"{srt_path.stem}_workspace"
            chunks_dir = workspace_dir / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)

            # Create 2 chunks: one success, one failed
            (chunks_dir / "chunk_001.txt").write_text("Hello.", encoding="utf-8")
            (chunks_dir / "chunk_001_chinese.txt").write_text("你好。", encoding="utf-8")
            # chunk_002 missing _chinese.txt

            worker._update_job(jid, status="failed", stage="translating",
                               progress="1/2", error="翻译失败")

            st, body = _post(19890, f"/job/{jid}/retry")
            # Should accept (200) — retry starts in background
            # The retry will fail on actual translation (no API), but the endpoint
            # should accept the request and set status to in_progress
            assert st in (200, 202), f"want 200/202 got {st}: {body}"

            # Job should now be in_progress (claimed by retry)
            j = worker._get_job(jid)
            assert j["status"] == "in_progress", f"status={j['status']}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_retry_nonexistent_job,
        test_retry_wrong_stage,
        test_retry_in_progress_rejected,
        test_retry_validation_progress_broken,
        test_retry_starts_background,
    ]
    fail = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            fail += 1
        except Exception as e:
            import traceback
            print(f"ERROR {t.__name__}: {e}")
            traceback.print_exc()
            fail += 1
    print()
    if fail:
        print(f"{fail}/{len(tests)} FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed")
