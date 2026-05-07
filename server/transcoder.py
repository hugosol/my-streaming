import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class Transcoder:
    def __init__(self, temp_root: Path):
        self.temp_root = temp_root
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()

    def get_temp_dir(self, index: str) -> Path:
        d = self.temp_root / index
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _read_log_tail(self, index: str, n: int = 15):
        log_path = self.temp_root / index / "ffmpeg.log"
        if not log_path.exists():
            return []
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return lines[-n:] if len(lines) > n else lines
        except OSError:
            return []

    def is_running(self, index: str) -> bool:
        with self._lock:
            proc = self._processes.get(index)
            return proc is not None and proc.poll() is None

    def start(self, index: str, video_path: str):
        exit_code = None
        crash_log_lines = None
        with self._lock:
            proc = self._processes.get(index)
            if proc is not None:
                if proc.poll() is None:
                    return
                exit_code = proc.poll()
                if exit_code != 0:
                    crash_log_lines = self._read_log_tail(index)
                else:
                    return
            elif (self.temp_root / index / "playlist.m3u8").exists():
                return
            temp_dir = self.get_temp_dir(index)
            self._clean_dir(temp_dir)

            playlist = temp_dir / "playlist.m3u8"
            segment_pattern = temp_dir / "segment_%d.ts"
            log_file = temp_dir / "ffmpeg.log"

            cmd = [
                "ffmpeg", "-y",
                "-hwaccel", "cuda",
                "-hwaccel_output_format", "cuda",
                "-i", video_path,
                "-vf", "scale_cuda=1280:-2",
                "-c:v", "h264_nvenc", "-cq", "23", "-preset", "p4",
                "-c:a", "copy",
                "-f", "hls",
                "-hls_time", "10",
                "-hls_list_size", "0",
                "-hls_segment_filename", str(segment_pattern),
                str(playlist),
            ]

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

            with open(log_file, "w") as log:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    creationflags=creationflags,
                )
            self._processes[index] = proc

        if crash_log_lines:
            _print_crash(index, exit_code, crash_log_lines)

    def stop(self, index: str):
        with self._lock:
            proc = self._processes.pop(index, None)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def stop_all(self):
        with self._lock:
            indices = list(self._processes.keys())
        for index in indices:
            self.stop(index)

    def cleanup_expired(self, max_age_seconds: float = 86400):
        now = time.time()
        if not self.temp_root.exists():
            return
        for item in self.temp_root.iterdir():
            try:
                if item.is_dir():
                    if now - item.stat().st_mtime > max_age_seconds:
                        shutil.rmtree(item)
                elif item.is_file():
                    if now - item.stat().st_mtime > max_age_seconds:
                        item.unlink()
            except OSError:
                pass

    def _clean_dir(self, d: Path):
        if not d.exists():
            return
        for item in d.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)


def _print_crash(index: str, exit_code: int, log_lines):
    print(f"\n[!] FFmpeg exited with code {exit_code} for video #{index} -- restarting...")
    for line in log_lines:
        print(f"    {line.rstrip()}")
    print()
