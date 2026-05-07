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

_INDEX_TPL = (_TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
_PLAYER_TPL = (_TEMPLATE_DIR / "player.html").read_text(encoding="utf-8")

_INDEX_ITEM = '<div style="padding:14px 0;border-bottom:1px solid #2a2a2a;"><a href="/play/{{id}}" style="font-size:17px;">{{name}}</a>{{sub_badge}}</div>'
_SUB_BADGE = ' <span style="color:#888;font-size:12px;">CC</span>'

_SEGMENT_RE = re.compile(r"^/stream/([a-f0-9]+)/segment_\d+\.ts$")
_PLAYLIST_RE = re.compile(r"^/stream/([a-f0-9]+)/playlist\.m3u8$")
_VTT_RE = re.compile(r"^/stream/([a-f0-9]+)/subtitles\.vtt$")


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
            for video_id, v in sorted(videos.items(), key=lambda x: x[1].name):
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
                sub_toggle_display="inline-block" if v.has_subtitle else "none",
                sub_url=f"/stream/{video_id}/subtitles.vtt" if v.has_subtitle else "",
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
                        convert_srt_to_vtt(v.subtitle, str(vtt_path))

            playlist_path = transcoder.get_temp_dir(video_id) / "playlist.m3u8"
            waited = 0
            while not playlist_path.exists() and waited < 30:
                time.sleep(0.1)
                waited += 1

            if not playlist_path.exists():
                self._serve_404()
                return

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
                self._serve_404()
                return
            if not v.has_subtitle:
                self._serve_404()
                return
            temp_dir = transcoder.get_temp_dir(video_id)
            vtt_path = temp_dir / "subtitles.vtt"
            if not vtt_path.exists():
                convert_srt_to_vtt(v.subtitle, str(vtt_path))
            content = vtt_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/vtt; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)

        def _serve_file(self, url_path, content_type):
            relative = url_path[len("/stream/"):]
            file_path = temp_root / relative
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
