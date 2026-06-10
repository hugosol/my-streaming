"""Tests for delete job endpoint (Slice 02).

Tests HTTP DELETE endpoint via running a temporary worker server.
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
    """Start worker HTTP server in a background thread with test config."""
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


def test_delete_nonexistent_job():
    tmpdir = tempfile.mkdtemp(prefix="td_")
    try:
        srv = _start_worker(tmpdir, 19876)
        try:
            st, body = _post(19876, "/job/deadbeef0000/delete")
            assert st == 404, f"want 404 got {st}: {body}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_delete_failed_job():
    tmpdir = tempfile.mkdtemp(prefix="td_")
    try:
        srv = _start_worker(tmpdir, 19877)
        try:
            st, body = _post(19877, "/job", {"url": "https://youtube.com/watch?v=abcd1234567"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]
            jdir = Path(tmpdir) / "jobs" / jid
            jdir.mkdir(parents=True, exist_ok=True)
            (jdir / "x.txt").write_text("hi")
            worker._update_job(jid, status="failed", stage="downloading", error="e")
            st, body = _post(19877, f"/job/{jid}/delete")
            assert st == 200, f"delete fail: {body}"
            assert worker._get_job(jid) is None
            assert not jdir.exists()
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_delete_in_progress_rejected():
    tmpdir = tempfile.mkdtemp(prefix="td_")
    try:
        srv = _start_worker(tmpdir, 19878)
        try:
            st, body = _post(19878, "/job", {"url": "https://youtube.com/watch?v=abcd1234568"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]
            st, body = _post(19878, f"/job/{jid}/delete")
            assert st == 409, f"want 409 got {st}: {body}"
            j = worker._get_job(jid)
            assert j and j["status"] == "in_progress"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_delete_success_rejected():
    tmpdir = tempfile.mkdtemp(prefix="td_")
    try:
        srv = _start_worker(tmpdir, 19879)
        try:
            st, body = _post(19879, "/job", {"url": "https://youtube.com/watch?v=abcd1234569"})
            assert st == 201, f"create fail: {body}"
            jid = body["job_id"]
            worker._update_job(jid, status="success", stage="done")
            st, body = _post(19879, f"/job/{jid}/delete")
            assert st == 409, f"want 409 got {st}: {body}"
        finally:
            _stop_worker(srv)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_delete_nonexistent_job,
        test_delete_failed_job,
        test_delete_in_progress_rejected,
        test_delete_success_rejected,
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
