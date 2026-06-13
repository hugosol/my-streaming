import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .scanner import VideoEntry, scan_directory
from .subtitle import convert_srt_to_vtt
from .transcoder import Transcoder


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_ITEM = '<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}<button class="job-btn video-delete-btn" data-video-md5="{{id}}">删除</button><button class="job-btn video-redownload-btn" data-video-md5="{{id}}">重新下载</button></div>'
_SUB_BADGE = ' <span class="sub-badge">CC</span>'

_JOB_ICONS = {
    "pending": "\u23f3",
    "downloading": "\u2b07",
    "punctuating": "\U0001F4DD",
    "resegmenting": "\u2702",
    "translating": "\U0001F504",
    "finalizing": "\U0001F4E6",
}

_JOB_LABELS = {
    "pending": "\u7b49\u5f85\u4e2d",
    "downloading": "\u4e0b\u8f7d\u4e2d",
    "punctuating": "\u6807\u70b9\u5904\u7406\u4e2d",
    "resegmenting": "\u91cd\u65b0\u5206\u53e5\u4e2d",
    "translating": "\u7ffb\u8bd1\u4e2d",
    "finalizing": "\u6536\u5c3e\u4e2d",
}
_INDEX_TPL = (_TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
_PLAYER_TPL = (_TEMPLATE_DIR / "player.html").read_text(encoding="utf-8")

_INDEX_ITEM = '<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}<button class="job-btn video-delete-btn" data-video-md5="{{id}}">删除</button><button class="job-btn video-redownload-btn" data-video-md5="{{id}}">重新下载</button></div>'
_SUB_BADGE = ' <span class="sub-badge">CC</span>'

_SEGMENT_RE = re.compile(r"^/stream/([a-f0-9]+)/segment_\d+\.ts$")
_PLAYLIST_RE = re.compile(r"^/stream/([a-f0-9]+)/playlist\.m3u8$")
_VTT_RE = re.compile(r"^/stream/([a-f0-9]+)/subtitles\.vtt$")
_EXTINF_RE = re.compile(r"#EXTINF:([\d.]+),\s*\n(segment_\d+\.ts)")
_JOB_DELETE_RE = re.compile(r"^/api/jobs/([a-f0-9]+)/delete$")
_JOB_RETRY_RE = re.compile(r"^/api/jobs/([a-f0-9]+)/retry$")
_VIDEO_DELETE_RE = re.compile(r"^/api/videos/([a-f0-9]+)/delete$")
_VIDEO_REDOWNLOAD_RE = re.compile(r"^/api/videos/([a-f0-9]+)/redownload$")
def _merge_playlists(static_path: Path, ffmpeg_path: Path, start_at: float = 0) -> str:
    static_text = static_path.read_text(encoding="utf-8")
    static_pairs = _EXTINF_RE.findall(static_text)

    ffmpeg_durations: dict[str, float] = {}
    if ffmpeg_path.exists():
        ffmpeg_text = ffmpeg_path.read_text(encoding="utf-8")
        for dur_str, name in _EXTINF_RE.findall(ffmpeg_text):
            ffmpeg_durations[name] = float(dur_str)

    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", "#EXT-X-PLAYLIST-TYPE:VOD"]
    if start_at > 0:
        lines.append(f"#EXT-X-START:TIME-OFFSET={start_at:.6f}")
    for dur_str, name in static_pairs:
        dur = ffmpeg_durations.get(name, float(dur_str))
        lines.append(f"#EXTINF:{dur:.6f},")
        lines.append(name)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _render(template: str, **kwargs) -> str:
    for key, value in kwargs.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def make_handler(dir_path: str, transcoder: Transcoder, temp_root: Path):
    # Load project config (lazy, from project root)
    _root = Path(__file__).parent.parent
    _config_path = _root / "config.json"
    _config: dict = {}
    _conn: sqlite3.Connection | None = None
    _conn_lock = threading.Lock()

    def _load_config() -> dict:
        nonlocal _config
        if not _config:
            try:
                with open(_config_path, "r", encoding="utf-8") as f:
                    _config = json.load(f)
            except Exception:
                pass
        return _config

    def _get_conn() -> sqlite3.Connection:
        nonlocal _conn
        if _conn is None:
            db_rel = _load_config().get("db_path", "db/jobs.db")
            db_path = Path(db_rel)
            if not db_path.is_absolute():
                db_path = _root / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(db_path), check_same_thread=False)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
        return _conn

    def _get_active_jobs() -> list[dict]:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id, url, video_id, stage, status, progress, video_name, error, created_at, updated_at "
                "FROM jobs WHERE status IN ('in_progress', 'failed') ORDER BY created_at DESC"
            ).fetchall()
            cols = ["id", "url", "video_id", "stage", "status", "progress", "video_name", "error", "created_at", "updated_at"]
            return [dict(zip(cols, row)) for row in rows]
        except Exception:
            return []

    def _get_worker_port() -> int:
        return int(_load_config().get("worker_port", 8899))

    class StreamingHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _scan(self):
            entries = scan_directory(dir_path)
            return {v.id: v for v in entries}

        def do_GET(self):
            path = urlparse(self.path).path

            try:
                if path == "/":
                    self._serve_index()
                elif path == "/api/jobs":
                    self._serve_jobs()
                elif path.startswith("/play/"):
                    video_id = path.rsplit("/", 1)[-1]
                    self._serve_player(video_id)
                elif path.startswith("/static/"):
                    self._serve_static(path)
                elif path.startswith("/stream/"):
                    if m := _PLAYLIST_RE.match(path):
                        self._serve_playlist(m.group(1))
                    elif m := _SEGMENT_RE.match(path):
                        self._serve_file(path, "video/mp2t")
                    elif m := _VTT_RE.match(path):
                        self._serve_vtt(m.group(1))
                    else:
                        self._serve_404()
                else:
                    self._serve_404()
            except (ValueError, IndexError, KeyError):
                self._serve_404()

        def do_POST(self):
            path = urlparse(self.path).path

            # Route: delete job
            if m := _JOB_DELETE_RE.match(path):
                self._forward_job_action(m.group(1), "delete")
                return

            # Route: retry job
            if m := _JOB_RETRY_RE.match(path):
                self._forward_job_action(m.group(1), "retry")
                return

            # Route: video delete
            if m := _VIDEO_DELETE_RE.match(path):
                self._forward_video_action(m.group(1), "delete")
                return


            # Route: video redownload
            if m := _VIDEO_REDOWNLOAD_RE.match(path):
                self._forward_video_action(m.group(1), "redownload")
                return
            if path == "/api/jobs":
                self._submit_job()
            else:
                self._serve_404()
        def _serve_index(self):
            videos = self._scan()
            items_html = ""
            for video_id, v in videos.items():
                badge = _SUB_BADGE if v.has_subtitle else ""
                items_html += _render(_INDEX_ITEM, id=video_id, name=html.escape(v.name), sub_badge=badge)

            # Render active jobs
            active_jobs = _get_active_jobs()
            jobs_html = ""
            if active_jobs:
                jobs_html += '<div class="jobs-section"><h2>任务</h2>'
                for job in active_jobs:
                    stage = job.get("stage", "")
                    icon = _JOB_ICONS.get(stage, "\u23f3")
                    label = _JOB_LABELS.get(stage, stage)
                    progress = html.escape(job["progress"] or "")
                    error = html.escape(job["error"] or "")
                    video_name = html.escape(job["video_name"] or "")[:15]
                    video_id = html.escape(job["video_id"] or "")
                    name = video_name or video_id
                    is_failed = job.get("status") == "failed"

                    parts = [icon, label]
                    if progress:
                        parts.append(f"\uff08{progress}\uff09")
                    if name:
                        parts.append(name)
                    detail = f"\uff08{error}\uff09" if error else ""
                    failed_class = " job-failed" if is_failed else ""
                    job_id_attr = html.escape(job["id"])
                    stage_attr = html.escape(stage)
                    status_attr = html.escape(job.get("status", ""))

                    buttons_html = ""
                    if is_failed:
                        if stage == "translating":
                            buttons_html = (
                                f' <button class="job-btn job-retry-btn" data-job-id="{job_id_attr}">\u91cd\u8bd5</button>'
                                f' <button class="job-btn job-delete-btn" data-job-id="{job_id_attr}">\u5220\u9664</button>'
                            )
                        else:
                            buttons_html = f' <button class="job-btn job-delete-btn" data-job-id="{job_id_attr}">\u5220\u9664</button>'

                    jobs_html += (
                        f'<div class="job-item{failed_class}"'
                        f' data-job-id="{job_id_attr}"'
                        f' data-stage="{stage_attr}"'
                        f' data-status="{status_attr}">'
                        f'{" ".join(parts)}{detail}{buttons_html}'
                        f'<span class="job-msg"></span>'
                        f'</div>'
                    )

            html_content = _render(_INDEX_TPL, items=items_html, jobs=jobs_html)
            self._respond_html(html_content)

        def _serve_jobs(self):
            """GET /api/jobs — return active jobs as JSON."""
            jobs = _get_active_jobs()
            self._respond_json(jobs)

        def _submit_job(self):
            """POST /api/jobs — submit a new YouTube download task."""
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._respond_json({"error": "invalid JSON"}, 400)
                return

            url = data.get("url", "").strip()
            if not url:
                self._respond_json({"error": "missing url"}, 400)
                return

            port = _get_worker_port()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/job",
                    data=json.dumps({"url": url}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    status_code = resp.status
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                try:
                    result = json.loads(body)
                except Exception:
                    result = {"error": body}
                status_code = e.code
            except Exception as e:
                self._respond_json({"error": f"Worker unreachable: {e}"}, 503)
            self._respond_json(result, status_code)

        def _forward_job_action(self, job_id: str, action: str) -> None:
            """Forward job delete/retry to worker and relay response."""
            port = _get_worker_port()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/job/{job_id}/{action}",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    status_code = resp.status
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                try:
                    result = json.loads(body)
                except Exception:
                    result = {"error": body}
                status_code = e.code
            except Exception as e:
                self._respond_json({"error": f"Worker unreachable: {e}"}, 503)
                return

            self._respond_json(result, status_code)

        def _forward_video_action(self, video_md5: str, action: str) -> None:
            """Forward video delete/redownload to worker and relay response."""
            port = _get_worker_port()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/video/{action}",
                    data=json.dumps({"video_md5": video_md5}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    status_code = resp.status
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                try:
                    result = json.loads(body)
                except Exception:
                    result = {"error": body}
                status_code = e.code
            except Exception as e:
                self._respond_json({"error": f"Worker unreachable: {e}"}, 503)
                return

            self._respond_json(result, status_code)

        def _serve_player(self, video_id):
            videos = self._scan()
            v = videos.get(video_id)
            if v is None:
                self._serve_404()
                return
            html_content = _render(_PLAYER_TPL,
                title=html.escape(v.name),
                playlist_url=f"/stream/{video_id}/playlist.m3u8",
                video_id=video_id,
                subtitle_url=f"/stream/{video_id}/subtitles.vtt" if v.has_subtitle else "",
            )
            self._respond_html(html_content)

        def _serve_playlist(self, video_id):
            videos = self._scan()
            v = videos.get(video_id)
            if v is None:
                self._serve_404()
                return

            if not transcoder.is_running(video_id):
                transcoder.start(video_id, v.path)
                if v.has_subtitle:
                    vtt_path = transcoder.get_temp_dir(video_id) / "subtitles.vtt"
                    if not vtt_path.exists():
                        print(f"[VTT] {video_id} pre-convert from playlist handler")
                        try:
                            convert_srt_to_vtt(v.subtitle, str(vtt_path))
                        except Exception as e:
                            print(f"[VTT] {video_id} pre-convert error: {e}")

            playlist_path = transcoder.get_temp_dir(video_id) / "playlist.m3u8"
            waited = 0
            while not playlist_path.exists() and waited < 30:
                time.sleep(0.1)
                waited += 1

            if not playlist_path.exists():
                self._serve_404()
                return

            parsed = urlparse(self.path)
            start_at = float(parse_qs(parsed.query).get("start", ["0"])[0])

            temp_dir = transcoder.get_temp_dir(video_id)
            ffmpeg_path = temp_dir / "_ffmpeg.m3u8"
            if ffmpeg_path.exists():
                content = _merge_playlists(playlist_path, ffmpeg_path, start_at).encode("utf-8")
            else:
                content = playlist_path.read_bytes()
                if start_at > 0:
                    tag = f"\n#EXT-X-START:TIME-OFFSET={start_at:.6f}\n".encode("utf-8")
                    idx = content.find(b"\n#EXTINF:")
                    if idx >= 0:
                        content = content[:idx] + tag + content[idx:]

            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

        def _serve_vtt(self, video_id):
            videos = self._scan()
            v = videos.get(video_id)
            if v is None:
                print(f"[VTT] {video_id} video not found")
                self._serve_404()
                return
            if not v.has_subtitle:
                print(f"[VTT] {video_id} no subtitle file")
                self._serve_404()
                return
            temp_dir = transcoder.get_temp_dir(video_id)
            vtt_path = temp_dir / "subtitles.vtt"
            if not vtt_path.exists():
                print(f"[VTT] {video_id} converting {v.subtitle}")
                try:
                    convert_srt_to_vtt(v.subtitle, str(vtt_path))
                except Exception as e:
                    print(f"[VTT] {video_id} convert error: {e}")
                    self._serve_404()
                    return
            content = vtt_path.read_bytes()
            print(f"[VTT] {video_id} OK ({len(content)} bytes)")
            self.send_response(200)
            self.send_header("Content-Type", "text/vtt; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

        def _serve_file(self, url_path, content_type):
            relative = url_path[len("/stream/"):]
            file_path = temp_root / relative
            if not file_path.exists() and file_path.suffix == ".ts":
                video_dir = file_path.parent
                waited = 0
                while not file_path.exists() and waited < 600:
                    if not (video_dir / "playlist.m3u8").exists():
                        break
                    time.sleep(0.1)
                    waited += 1
            if not file_path.exists():
                self._serve_404()
                return
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

        def _serve_static(self, url_path):
            relative = url_path[len("/static/"):]
            file_path = _STATIC_DIR / relative
            if not file_path.is_file():
                self._serve_404()
                return
            ext = file_path.suffix.lower()
            content_types = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".png": "image/png",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }
            content_type = content_types.get(ext, "application/octet-stream")
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)

        def _serve_404(self):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 Not Found")

        def _respond_html(self, html_str):
            data = html_str.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _respond_json(self, data: dict | list, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return StreamingHandler
