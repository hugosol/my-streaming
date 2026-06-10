#!/usr/bin/env python3
"""Worker process for YouTube video download and subtitle translation.

Listens for POST /job from the streaming server, executes the pipeline:
  download -> punctuate -> resegment -> translate -> finalize

Progress is written to db/jobs.db (shared with the streaming server).
"""

import json
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
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                video_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress TEXT DEFAULT '',
                video_name TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        _conn.commit()
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
        "SELECT * FROM jobs WHERE video_id = ? AND status NOT IN ('done', 'failed')",
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
            "INSERT INTO jobs (id, url, video_id, status, progress, video_name, error, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', '', '', '', ?, ?)",
            (job_id, url, video_id, now, now),
        )
        conn.commit()
    return job_id


def _mark_interrupted() -> None:
    """On startup, mark all non-terminal jobs as failed."""
    now = _now_iso()
    with _conn_lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'Worker restarted', updated_at = ? "
            "WHERE status NOT IN ('done', 'failed')",
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
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    for line in proc.stdout:  # type: ignore[union-attr]
        stripped = line.rstrip()
        print(f"[{label}] {stripped}")
        if on_line:
            on_line(stripped)
    proc.wait()
    print(f"[{label}] Exit code: {proc.returncode}")
    return proc.returncode


def _run_punctuate_chunk(chunk_path: Path, output_path: Path) -> bool:
    """Call DeepSeek API to add punctuation to one chunk."""
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
            return True
    except Exception as e:
        print(f"[PUNCTUATE] Chunk {chunk_path.name} failed: {e}")
    return False


def _execute_pipeline(job_id: str, url: str) -> None:
    """Execute the full download->translate pipeline in a background thread."""
    video_dir = _video_dir()
    job_dir = _ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Download
        _update_job(job_id, status="downloading", progress="")
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
            return

        # Find English SRT
        srt_files = sorted(job_dir.glob("*.en.srt"))
        if not srt_files:
            # Try any SRT
            srt_files = sorted(job_dir.glob("*.srt"))
        if not srt_files:
            mp4_files = sorted(job_dir.glob("*.mp4"))
            vname = mp4_files[0].stem if mp4_files else ""
            _update_job(job_id, status="done", progress="", video_name=vname)
            for mp4 in mp4_files:
                mp4.replace(video_dir / mp4.name)
            return

        srt_path = srt_files[0]

        # Set video_name from downloaded MP4
        mp4_files = sorted(job_dir.glob("*.mp4"))
        if mp4_files:
            _update_job(job_id, video_name=mp4_files[0].stem)

        # Step 2: Punctuate
        _update_job(job_id, status="punctuating", progress="")
        
        # Check if punctuation is needed
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

        if needs_punct:
            srt_marker = _SKILLS_DIR / "srt-punctuator" / "scripts" / "srt_marker.py"
            
            # Prepare chunks
            rc = _run_subprocess(
                [sys.executable, str(srt_marker), "prepare", str(srt_path)],
                job_dir, "PUNCT-PREPARE",
            )
            if rc != 0:
                _update_job(job_id, status="failed", error="标点准备失败")
                return

            # Process chunks via DeepSeek API
            chunks_json = srt_path.parent / f"{srt_path.stem}.punc_work" / "chunks.json"
            if chunks_json.exists():
                chunks_data = json.loads(chunks_json.read_text())
                total_chunks = chunks_data.get("total_chunks", 0)
                chunks_dir = chunks_json.parent / "chunks"
                
                for i in range(total_chunks):
                    chunk_file = chunks_dir / f"chunk_{i:03d}.txt"
                    output_file = chunks_dir / f"chunk_{i:03d}_punctuated.txt"
                    if not chunk_file.exists():
                        _update_job(job_id, status="failed", error=f"缺少chunk文件: {chunk_file.name}")
                        return
                    
                    _update_job(job_id, progress=f"{i + 1}/{total_chunks}")
                    if not _run_punctuate_chunk(chunk_file, output_file):
                        _update_job(job_id, status="failed", error=f"标点处理失败 (chunk {i + 1}/{total_chunks})")
                        return
            else:
                _update_job(job_id, status="failed", error="chunks.json not found after prepare")
                return

            # Finalize
            punc_work_dir = srt_path.parent / f"{srt_path.stem}.punc_work"
            rc = _run_subprocess(
                [sys.executable, str(srt_marker), "finalize", str(srt_path), str(punc_work_dir), "--from-chunks"],
                job_dir, "PUNCT-FINALIZE",
            )
            if rc != 0:
                _update_job(job_id, status="failed", error="标点合并失败")
                return
            
            _update_job(job_id, progress="")
        else:
            _update_job(job_id, progress="")

        # Step 3: Resegment
        _update_job(job_id, status="resegmenting", progress="")
        resegment_script = _SCRIPTS_DIR / "resegment.py"
        rc = _run_subprocess(
            [sys.executable, str(resegment_script), str(srt_path)],
            job_dir, "RESEGMENT",
        )
        if rc != 0:
            _update_job(job_id, status="failed", error="重新分句失败")
            return

        # Step 4: Translate
        _update_job(job_id, status="translating", progress="")
        batch_script = _SCRIPTS_DIR / "batch_translate.py"
        db_path = str(_db_path())
        rc = _run_subprocess(
            [sys.executable, str(batch_script), str(srt_path),
             "--job-id", job_id, "--db-path", db_path],
            job_dir, "TRANSLATE",
        )
        if rc != 0:
            _update_job(job_id, status="failed", error="翻译失败")
            return

        # Step 5: Finalize
        _update_job(job_id, status="finalizing", progress="")
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
        # Move final files to video directory
        for mp4 in job_dir.glob("*.mp4"):
            dest = video_dir / mp4.name
            if not dest.exists():
                mp4.replace(dest)
        
        # Move SRT files (bilingual + original backup)
        for srt in job_dir.glob("*.srt"):
            dest = video_dir / srt.name
            srt.replace(dest)  # overwrite if exists

        video_name = ""
        mp4_files = sorted(video_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp4_files:
            video_name = mp4_files[0].name

        _update_job(job_id, status="done", progress="", video_name=video_name)

        # Clean up job directory
        shutil.rmtree(job_dir, ignore_errors=True)

    except Exception as e:
        traceback.print_exc()
        _update_job(job_id, status="failed", error=str(e))
        print(f"[PIPELINE] Unexpected error in job {job_id}: {e}")


# --- HTTP Server ---

class WorkerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def do_POST(self):
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

    def _respond(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
