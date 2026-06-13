#!/usr/bin/env python3
"""Worker process for YouTube video download and subtitle translation.

Listens for POST /job from the streaming server, executes the pipeline:
  download -> punctuate -> resegment -> translate -> finalize

Progress is written to db/jobs.db (shared with the streaming server).
"""

import json
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import shutil
# --- Paths ---
_ROOT = Path(__file__).parent
_CONFIG_PATH = _ROOT / "config.json"
_SCRIPTS_DIR = _ROOT / "worker" / "scripts"
_SKILLS_DIR = _ROOT / "worker" / "skills"
_config: dict = {}


def _load_config() -> dict:
    global _config
    if not _config:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config = json.load(f)
    return _config


def _db_path() -> Path:
    db_rel = _load_config().get("db_path", "db/jobs.db")
    p = Path(db_rel)
    if not p.is_absolute():
        p = _ROOT / p
    return p


def _video_dir() -> Path:
    vd = _load_config().get("video_dir", ".")
    p = Path(vd)
    if not p.is_absolute():
        p = _ROOT / p
    return p


def _worker_port() -> int:
    return int(_load_config().get("worker_port", 8899))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Database ---

_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        dbp = _db_path()
        dbp.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(dbp), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        # Check if old schema needs migration (no stage column)
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if cols and "stage" not in cols:
            _conn.execute("DROP TABLE jobs")
            _conn.commit()
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                video_id TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'pending',
                status TEXT NOT NULL DEFAULT 'in_progress',
                progress TEXT DEFAULT '',
                video_name TEXT DEFAULT '',
                video_md5 TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Migrate: add video_md5 column if missing
        cols = [row[1] for row in _conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if cols and "video_md5" not in cols:
            _conn.execute("ALTER TABLE jobs ADD COLUMN video_md5 TEXT DEFAULT ''")
    return _conn


def _update_job(job_id: str, **kwargs) -> None:
    """Update job fields. kwargs keys must match column names."""
    if not kwargs:
        return
    kwargs["updated_at"] = _now_iso()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    with _conn_lock:
        conn = _get_conn()
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
        conn.commit()


def _get_job(job_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM jobs LIMIT 0").description]
    return dict(zip(cols, row))


def _extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""

def _find_duplicate(video_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM jobs WHERE video_id = ? AND status = 'in_progress'",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in conn.execute("SELECT * FROM jobs LIMIT 0").description]
    return dict(zip(cols, row))

def _create_job(url: str, video_id: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    with _conn_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO jobs (id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', 'in_progress', '', '', '', ?, ?)",
            (job_id, url, video_id, now, now),
        )
        conn.commit()
    return job_id


def _mark_interrupted() -> None:
    """On startup, mark all in_progress jobs as failed."""
    now = _now_iso()
    with _conn_lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'Worker restarted', updated_at = ? "
            "WHERE status = 'in_progress'",
            (now,),
        )
        conn.commit()


# --- Pipeline ---

def _run_subprocess(cmd: list[str], cwd: Path, label: str, on_line: object = None) -> int:
    """Run a subprocess with live output streaming. Returns exit code.
    
    Args:
        on_line: Optional callback(str) invoked for each output line.
    """
    print(f"[{label}] Starting: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8:replace"
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=env,
    )
    for line in proc.stdout:  # type: ignore[union-attr]
        stripped = line.rstrip()
        print(f"[{label}] {stripped}")
        if on_line:
            on_line(stripped)
    proc.wait()
    print(f"[{label}] Exit code: {proc.returncode}")
    return proc.returncode


def _run_punctuate_chunk(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
    """Call DeepSeek API to add punctuation to one chunk.
    Returns (True, "") on success, (False, error_reason) on failure.
    """
    try:
        chunk_text = chunk_path.read_text(encoding="utf-8")
        from worker.skill_caller import call_skill

        result = call_skill(
            "srt-punctuator",
            f"为以下SRT字幕块添加英文标点符号，保持 <<N>> 标记不变：\n\n{chunk_text}",
            "请直接输出加好标点的完整文本，不要添加任何解释或额外内容。",
            max_tokens=32768,
        )
        if result:
            output_path.write_text(result, encoding="utf-8")
            return True, ""
        return False, "Empty response from API"
    except Exception as e:
        print(f"[PUNCTUATE] Chunk {chunk_path.name} failed: {e}")
        return False, str(e)


def _execute_pipeline(job_id: str, url: str) -> None:
    """Execute the full pipeline in a background thread (thin orchestrator)."""
    job_dir = _ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        srt_path = _do_download(job_id, url)
        if srt_path is None:
            return

        if not _do_punctuate(job_id, srt_path):
            return
        if not _do_resegment(job_id, srt_path):
            return
        if not _do_translate(job_id, srt_path):
            return
        if not _do_finalize(job_id, srt_path):
            return
    except Exception as e:
        traceback.print_exc()
        _update_job(job_id, status="failed", error=str(e))
        print(f"[PIPELINE] Unexpected error in job {job_id}: {e}")


def _do_download(job_id: str, url: str) -> Path | None:
    """Download video + SRT. Returns srt_path on success, None on failure/no-SRT."""
    video_dir = _video_dir()
    job_dir = _ROOT / "jobs" / job_id
    _update_job(job_id, stage="downloading", progress="")

    download_script = _SCRIPTS_DIR / "download.ps1"
    proxy = _load_config().get("yt_download_proxy", "")
    download_cmd = [
        "powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(download_script),
        "-Url", url,
        "-OutputDir", str(job_dir),
    ]
    if proxy:
        download_cmd.extend(["-Proxy", proxy])

    rc = _run_subprocess(download_cmd, job_dir, "DOWNLOAD")
    if rc != 0:
        _update_job(job_id, status="failed", error=f"下载失败 (exit code {rc})")
        return None

    srt_files = sorted(job_dir.glob("*.en.srt"))
    if not srt_files:
        srt_files = sorted(job_dir.glob("*.srt"))
    if not srt_files:
        mp4_files = sorted(job_dir.glob("*.mp4"))
        vname = mp4_files[0].stem if mp4_files else ""
        vmd5 = hashlib.md5(mp4_files[0].name.encode()).hexdigest()[:8] if mp4_files else ""
        _update_job(job_id, stage="done", status="success", progress="", video_name=vname, video_md5=vmd5)
        for mp4 in mp4_files:
            mp4.replace(video_dir / mp4.name)
        return None

    mp4_files = sorted(job_dir.glob("*.mp4"))
    if mp4_files:
        vmd5 = hashlib.md5(mp4_files[0].name.encode()).hexdigest()[:8]
        _update_job(job_id, video_name=mp4_files[0].stem, video_md5=vmd5)


def _do_punctuate(job_id: str, srt_path: Path) -> bool:
    """Punctuation check and DeepSeek chunk processing. Returns True on success."""
    _update_job(job_id, stage="punctuating", progress="")

    text = srt_path.read_text(encoding="utf-8")
    lines = [l for l in text.split("\n") if l.strip() and not l.strip().isdigit()
             and "-->" not in l]
    if lines:
        punct_count = sum(1 for l in lines for c in l if c in ".?!,;:")
        expected = len(lines) * _load_config().get("punctuation_check", {}).get("expected_per_lines", 0.333)
        threshold = expected * _load_config().get("punctuation_check", {}).get("threshold_factor", 0.4)
        needs_punct = punct_count < threshold
    else:
        needs_punct = False

    if not needs_punct:
        _update_job(job_id, progress="")
        return True

    srt_marker = _SKILLS_DIR / "srt-punctuator" / "scripts" / "srt_marker.py"
    job_dir = srt_path.parent

    rc = _run_subprocess(
        [sys.executable, str(srt_marker), "prepare", str(srt_path)],
        job_dir, "PUNCT-PREPARE",
    )
    if rc != 0:
        _update_job(job_id, status="failed", error="标点准备失败")
        return False

    chunks_json = job_dir / f"{srt_path.stem}.punc_work" / "chunks.json"
    if not chunks_json.exists():
        _update_job(job_id, status="failed", error="chunks.json not found after prepare")
        return False

    chunks_data = json.loads(chunks_json.read_text())
    total_chunks = chunks_data.get("total_chunks", 0)
    chunks_dir = chunks_json.parent / "chunks"

    for i in range(total_chunks):
        chunk_file = chunks_dir / f"chunk_{i:03d}.txt"
        output_file = chunks_dir / f"chunk_{i:03d}_punctuated.txt"
        if not chunk_file.exists():
            _update_job(job_id, status="failed", error=f"缺少chunk文件: {chunk_file.name}")
            return False
        _update_job(job_id, progress=f"{i + 1}/{total_chunks}")
        ok, err_msg = _run_punctuate_chunk(chunk_file, output_file)
        if not ok:
            _update_job(job_id, status="failed", error=f"标点处理失败 (chunk {i + 1}/{total_chunks}): {err_msg}")
            return False

    punc_work_dir = job_dir / f"{srt_path.stem}.punc_work"
    rc = _run_subprocess(
        [sys.executable, str(srt_marker), "finalize", str(srt_path), str(punc_work_dir), "--from-chunks"],
        job_dir, "PUNCT-FINALIZE",
    )
    if rc != 0:
        _update_job(job_id, status="failed", error="标点合并失败")
        return False

    _update_job(job_id, progress="")
    return True


def _do_resegment(job_id: str, srt_path: Path) -> bool:
    """Re-segment the SRT. Returns True on success."""
    _update_job(job_id, stage="resegmenting", progress="")
    resegment_script = _SCRIPTS_DIR / "resegment.py"
    rc = _run_subprocess(
        [sys.executable, str(resegment_script), str(srt_path)],
        srt_path.parent, "RESEGMENT",
    )
    if rc != 0:
        _update_job(job_id, status="failed", error="重新分句失败")
        return False
    return True


def _do_translate(job_id: str, srt_path: Path) -> bool:
    """Run batch translation. Returns True on success."""
    _update_job(job_id, stage="translating", progress="")
    batch_script = _SCRIPTS_DIR / "batch_translate.py"
    db_path = str(_db_path())
    rc = _run_subprocess(
        [sys.executable, str(batch_script), str(srt_path),
         "--job-id", job_id, "--db-path", db_path],
        srt_path.parent, "TRANSLATE",
    )
    if rc != 0:
        _update_job(job_id, status="failed", error="翻译失败")
        return False
    return True


def _do_finalize(job_id: str, srt_path: Path) -> bool:
    """Aggregate, combine, finalize, move files, clean up. Returns True on success."""
    video_dir = _video_dir()
    job_dir = srt_path.parent
    _update_job(job_id, stage="finalizing", progress="")

    finalize_script = _SCRIPTS_DIR / "finalize-subtitles.ps1"
    workspace_dir = job_dir / f"{srt_path.stem}_workspace"
    rc = _run_subprocess(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(finalize_script),
         "-InputFile", str(srt_path),
         "-WorkspaceDir", str(workspace_dir)],
        job_dir, "FINALIZE",
    )
    if rc != 0:
        print(f"[FINALIZE] Warning: exit code {rc} (non-fatal)")

    for mp4 in job_dir.glob("*.mp4"):
        dest = video_dir / mp4.name
        if not dest.exists():
            mp4.replace(dest)

    # Move only the active SRT (not -bak / -src backups)
    for srt in job_dir.glob("*.srt"):
        if "-bak" in srt.stem or "-src" in srt.stem:
            continue
        dest = video_dir / srt.name
        srt.replace(dest)

    video_name = ""
    mp4_files = sorted(video_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp4_files:
        video_name = mp4_files[0].name

    _update_job(job_id, stage="done", status="success", progress="", video_name=video_name)
    shutil.rmtree(job_dir, ignore_errors=True)
    return True


def _do_retry(job_id: str) -> None:
    """Retry failed chunks of a translating-stage job, then re-finalize."""
    try:
        job = _get_job(job_id)
        if not job:
            return

        # 1. Read progress A/B
        progress = job.get("progress", "")
        if "/" not in progress:
            _update_job(job_id, status="failed", error="retry: invalid progress format")
            return
        a_str, b_str = progress.split("/", 1)
        A = int(a_str)
        B = int(b_str)

        if A >= B:
            _update_job(job_id, status="failed", error=f"retry: A({A}) >= B({B})")
            return

        # Find srt_path from job directory (exclude backup files)
        job_dir = _ROOT / "jobs" / job_id
        srt_files = sorted(job_dir.glob("*.srt"))
        srt_files = [f for f in srt_files if "-bak" not in f.stem]
        if not srt_files:
            _update_job(job_id, status="failed", error="retry: SRT file not found")
            return
        srt_path = srt_files[0]

        workspace_dir = job_dir / f"{srt_path.stem}_workspace"
        chunks_dir = workspace_dir / "chunks"
        if not chunks_dir.exists():
            _update_job(job_id, status="failed", error="retry: chunks directory not found")
            return

        # 2. Count chunk files -> B_actual (exclude _chinese.txt output files)
        chunk_files = sorted(chunks_dir.glob("chunk_*.txt"))
        chunk_files = [f for f in chunk_files if "_chinese" not in f.stem]
        B_actual = len(chunk_files)
        if B != B_actual:
            _update_job(job_id, status="failed",
                        error=f"retry: B mismatch (DB={B}, actual={B_actual})")
            return

        # 3. Count successful chunks (chinese.txt exists + line count matches)
        A_actual = 0
        failed_indices = []
        for cf in chunk_files:
            chinese = chunks_dir / f"{cf.stem}_chinese.txt"
            if chinese.exists():
                try:
                    src_lines = len([l for l in cf.read_text(encoding="utf-8").split("\n") if l.strip()])
                    out_lines = len([l for l in chinese.read_text(encoding="utf-8").split("\n") if l.strip()])
                    if src_lines == out_lines:
                        A_actual += 1
                        continue
                except Exception:
                    pass
            failed_indices.append(cf)

        if A != A_actual:
            _update_job(job_id, status="failed",
                        error=f"retry: A mismatch (DB={A}, actual={A_actual})")
            return

        # 4. Delete failed _chinese.txt files
        for cf in failed_indices:
            chinese = chunks_dir / f"{cf.stem}_chinese.txt"
            if chinese.exists():
                chinese.unlink()

        # 5. Serially retranslate failed chunks
        from worker.translate import translate_chunk
        for cf in failed_indices:
            chinese = chunks_dir / f"{cf.stem}_chinese.txt"
            ok, err_msg = translate_chunk(cf, chinese)
            if not ok:
                _update_job(job_id, status="failed",
                            error=f"retry: chunk {cf.name} failed: {err_msg}")
                return
            A += 1
            _update_job(job_id, progress=f"{A}/{B}")

        # 6. Aggregate _chinese.txt chunks and combine back to bilingual SRT
        original_txt = workspace_dir / f"{srt_path.stem}_original.txt"
        chinese_txt = workspace_dir / f"{srt_path.stem}_chinese.txt"

        chunk_files_sorted = sorted(
            [f for f in chunks_dir.glob("chunk_*.txt") if "_chinese" not in f.stem]
        )
        with open(chinese_txt, "w", encoding="utf-8") as outf:
            for cf in chunk_files_sorted:
                chinese_chunk = chunks_dir / f"{cf.stem}_chinese.txt"
                if chinese_chunk.exists():
                    outf.write(chinese_chunk.read_text(encoding="utf-8").rstrip("\n"))
                    outf.write("\n")

        combine_script = _SCRIPTS_DIR / "combine-subtitles.ps1"
        rc = _run_subprocess(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(combine_script),
             "-InputFile", str(srt_path),
             "-OriginalText", str(original_txt),
             "-ChineseText", str(chinese_txt)],
            job_dir, "COMBINE-RETRY",
        )
        if rc != 0:
            _update_job(job_id, status="failed", error="双语字幕合并失败")
            return

        # 7. Re-finalize
        if not _do_finalize(job_id, srt_path):
            return

    except Exception as e:
        traceback.print_exc()
        _update_job(job_id, status="failed", error=f"retry error: {e}")



def _cleanup_video_files(video_md5: str) -> bool:
    """Delete matching MP4 + SRT files from video_dir and temp/<video_md5>/ cache.
    Returns True if any files were deleted."""
    deleted = False
    video_dir = _video_dir()
    video_dir_path = Path(video_dir)
    _SUBTITLE_EXTS = {".srt", ".ass"}
    if video_dir_path.is_dir():
        for f in list(video_dir_path.iterdir()):
            fname = f.name
            f_md5 = hashlib.md5(fname.encode()).hexdigest()[:8]
            if f_md5 == video_md5:
                f.unlink(missing_ok=True)
                deleted = True
                stem = f.stem
                for sub_f in list(video_dir_path.iterdir()):
                    if sub_f.suffix.lower() in _SUBTITLE_EXTS and sub_f.stem == stem:
                        sub_f.unlink(missing_ok=True)
    temp_dir = _ROOT / "temp" / video_md5
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
        deleted = True
    return deleted
 # --- HTTP Server ---

_DELETE_JOB_RE = re.compile(r"^/job/([a-f0-9]+)/delete$")
_RETRY_JOB_RE = re.compile(r"^/job/([a-f0-9]+)/retry$")
_VIDEO_DELETE_RE = re.compile(r"^/video/delete$")
_VIDEO_REDOWNLOAD_RE = re.compile(r"^/video/redownload$")


class WorkerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def do_POST(self):
        # Route: DELETE job
        if m := _DELETE_JOB_RE.match(self.path):
            self._handle_delete(m.group(1))
            return


        # Route: RETRY job
        if m := _RETRY_JOB_RE.match(self.path):
            self._handle_retry(m.group(1))
            return

        # Route: VIDEO delete
        if m := _VIDEO_DELETE_RE.match(self.path):
            self._handle_video_delete()
            return

        # Route: VIDEO redownload
        if m := _VIDEO_REDOWNLOAD_RE.match(self.path):
            self._handle_video_redownload()
            return

        # Route: create job
        if self.path != "/job":
            self._respond(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, Exception):
            self._respond(400, {"error": "invalid JSON"})
            return
        url = data.get("url", "").strip()
        if not url:
            self._respond(400, {"error": "missing url"})
            return

        # Strip query params (keep v= parameter on watch URLs)
        if "youtube.com/watch?v=" in url and "&" in url:
            url = url.split("&")[0]
        elif "youtube.com/watch?v=" not in url and "?" in url:
            url = url.split("?")[0]

        video_id = _extract_video_id(url)
        if not video_id:
            self._respond(400, {"error": "invalid YouTube URL"})
            return

        dup = _find_duplicate(video_id)
        if dup:
            self._respond(409, {
                "error": "duplicate",
                "job_id": dup["id"],
                "status": dup["status"],
            })
            return

        job_id = _create_job(url, video_id)

        # Start pipeline in background thread
        thread = threading.Thread(target=_execute_pipeline, args=(job_id, url), daemon=True)
        thread.start()

        self._respond(201, {"job_id": job_id})

    def _handle_delete(self, job_id: str) -> None:
        """POST /job/<job_id>/delete — delete a failed job."""
        job = _get_job(job_id)
        if job is None:
            self._respond(404, {"error": "job not found"})
            return

        # Atomically claim the job: only proceed if status is 'failed'
        with _conn_lock:
            conn = _get_conn()
            cur = conn.execute(
                "UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status = 'failed'",
                (job_id,),
            )
            conn.commit()
            if cur.rowcount == 0:
                self._respond(409, {"error": "job is not in failed state"})
                return

        # Delete DB row
        with _conn_lock:
            conn = _get_conn()
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()

        # Delete job directory
        job_dir = _ROOT / "jobs" / job_id
        shutil.rmtree(job_dir, ignore_errors=True)

        self._respond(200, {"job_id": job_id, "deleted": True})

    def _handle_retry(self, job_id: str) -> None:
        """POST /job/<job_id>/retry — retry failed translation chunks."""
        job = _get_job(job_id)
        if job is None:
            self._respond(404, {"error": "job not found"})
            return

        if job["stage"] != "translating":
            self._respond(400, {"error": "retry only supported for translating stage"})
            return

        if job["status"] != "failed":
            self._respond(409, {"error": "job is not in failed state"})
            return

        # Atomically claim
        with _conn_lock:
            conn = _get_conn()
            cur = conn.execute(
                "UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status = 'failed'",
                (job_id,),
            )
            conn.commit()
            if cur.rowcount == 0:
                self._respond(409, {"error": "job is already being retried"})
                return

        # Start retry in background
        thread = threading.Thread(target=_do_retry, args=(job_id,), daemon=True)
        thread.start()
        self._respond(200, {"job_id": job_id, "status": "retrying"})

    def _respond(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _handle_video_delete(self) -> None:
        """POST /video/delete — delete a video and all associated data."""
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, Exception):
            self._respond(400, {"error": "invalid JSON"})
            return

        video_md5 = data.get("video_md5", "").strip()
        if not video_md5:
            self._respond(400, {"error": "missing video_md5"})
            return

        video_dir = _video_dir()
        deleted_any = False

        # 1. Find matching jobs (not in_progress) and delete them
        with _conn_lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id FROM jobs WHERE video_md5 = ? AND status != 'in_progress'",
                (video_md5,),
            ).fetchall()

        for (job_id,) in rows:
            with _conn_lock:
                conn = _get_conn()
                cur = conn.execute(
                    "UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status != 'in_progress'",
                    (job_id,),
                )
                conn.commit()
                if cur.rowcount == 0:
                    continue
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                conn.commit()
            job_dir = _ROOT / "jobs" / job_id
            shutil.rmtree(job_dir, ignore_errors=True)
            deleted_any = True

        # 2. Clean up video files and temp cache
        if _cleanup_video_files(video_md5):
            deleted_any = True

        if not deleted_any:
            self._respond(404, {"error": "no video found"})
            return

        self._respond(200, {"ok": True})

    def _handle_video_redownload(self) -> None:
        """POST /video/redownload — delete old data and re-trigger pipeline."""
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, Exception):
            self._respond(400, {"error": "invalid JSON"})
            return

        video_md5 = data.get("video_md5", "").strip()
        if not video_md5:
            self._respond(400, {"error": "missing video_md5"})
            return

        # 1. Find latest job by video_md5 (not in_progress)
        with _conn_lock:
            conn = _get_conn()
            row = conn.execute(
                "SELECT url, video_id FROM jobs WHERE video_md5 = ? AND status != 'in_progress' ORDER BY created_at DESC LIMIT 1",
                (video_md5,),
            ).fetchone()

        if row is None:
            self._respond(404, {"error": "no video record found for redownload"})
            return

        original_url, original_video_id = row

        # 2. Claim and delete all matching jobs
        with _conn_lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id FROM jobs WHERE video_md5 = ? AND status != 'in_progress'",
                (video_md5,),
            ).fetchall()

        for (job_id,) in rows:
            with _conn_lock:
                conn = _get_conn()
                cur = conn.execute(
                    "UPDATE jobs SET status = 'in_progress' WHERE id = ? AND status != 'in_progress'",
                    (job_id,),
                )
                conn.commit()
                if cur.rowcount == 0:
                    continue
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                conn.commit()
            job_dir = _ROOT / "jobs" / job_id
            shutil.rmtree(job_dir, ignore_errors=True)

        # 3. Clean up video files and temp cache
        _cleanup_video_files(video_md5)

        # 4. Create new job and start pipeline
        job_id = _create_job(original_url, original_video_id)
        thread = threading.Thread(target=_execute_pipeline, args=(job_id, original_url), daemon=True)
        thread.start()

        self._respond(200, {"ok": True})

def main():
    _mark_interrupted()

    port = _worker_port()
    server = HTTPServer(("127.0.0.1", port), WorkerHandler)
    print(f"Worker listening on http://127.0.0.1:{port}/job")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWorker shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
