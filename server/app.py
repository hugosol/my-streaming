import html
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .scanner import VideoEntry, scan_directory
from .subtitle import convert_srt_to_vtt
from .transcoder import Transcoder


_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

_INDEX_TPL = (_TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
_PLAYER_TPL = (_TEMPLATE_DIR / "player.html").read_text(encoding="utf-8")

_INDEX_ITEM = '<div class="index-item"><a href="/play/{{id}}">{{name}}</a>{{sub_badge}}</div>'
_SUB_BADGE = ' <span class="sub-badge">CC</span>'

_SEGMENT_RE = re.compile(r"^/stream/([a-f0-9]+)/segment_\d+\.ts$")
_PLAYLIST_RE = re.compile(r"^/stream/([a-f0-9]+)/playlist\.m3u8$")
_VTT_RE = re.compile(r"^/stream/([a-f0-9]+)/subtitles\.vtt$")
_EXTINF_RE = re.compile(r"#EXTINF:([\d.]+),\s*\n(segment_\d+\.ts)")


def _merge_playlists(static_path: Path, ffmpeg_path: Path) -> str:
    static_text = static_path.read_text(encoding="utf-8")
    static_pairs = _EXTINF_RE.findall(static_text)

    ffmpeg_durations: dict[str, float] = {}
    if ffmpeg_path.exists():
        ffmpeg_text = ffmpeg_path.read_text(encoding="utf-8")
        for dur_str, name in _EXTINF_RE.findall(ffmpeg_text):
            ffmpeg_durations[name] = float(dur_str)

    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", "#EXT-X-PLAYLIST-TYPE:VOD"]
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

        def _serve_index(self):
            videos = self._scan()
            items_html = ""
            for video_id, v in videos.items():
                badge = _SUB_BADGE if v.has_subtitle else ""
                items_html += _render(_INDEX_ITEM, id=video_id, name=html.escape(v.name), sub_badge=badge)
            html_content = _render(_INDEX_TPL, items=items_html)
            self._respond_html(html_content)

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
                sub_track=f'<track kind="subtitles" src="/stream/{video_id}/subtitles.vtt" srclang="en" label="Subtitles" default>' if v.has_subtitle else "",
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

            temp_dir = transcoder.get_temp_dir(video_id)
            ffmpeg_path = temp_dir / "_ffmpeg.m3u8"
            if ffmpeg_path.exists():
                content = _merge_playlists(playlist_path, ffmpeg_path).encode("utf-8")
            else:
                content = playlist_path.read_bytes()

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
            self.end_headers()
            self.wfile.write(data)

    return StreamingHandler
