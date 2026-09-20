"""Regression tests for silent subtitle loss during download.

Bug: YouTube's default yt-dlp clients withhold automatic captions unless a PO
token is supplied, and yt-dlp still exits 0 with no subtitle file. The pipeline
therefore downloaded the MP4, skipped all subtitle stages, and reported success
("只完成下载，没有任何字幕文件，后台也没有报错").

Contract under test:
- download.ps1 retries a subtitle-only download with the web_embedded client.
- If still no SRT exists, download.ps1 exits 3 (video downloaded, no subs).
- worker._do_download maps exit 3 (or a missing SRT) to a failed job with a
  clear error, keeping the downloaded video in the library.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_loader = importlib.machinery.SourceFileLoader("wm_download", str(Path(__file__).parent.parent / "worker.py"))
_spec = importlib.util.spec_from_loader("wm_download", _loader)
assert _spec is not None
worker = importlib.util.module_from_spec(_spec)
_loader.exec_module(worker)

_ROOT = Path(__file__).parent.parent
_DOWNLOAD_PS1 = _ROOT / "worker" / "scripts" / "download.ps1"
_POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")


def _install_fake_ytdlp(tmp_path: Path, produce_subtitles: bool) -> dict[str, str]:
    """Put a fake yt-dlp on PATH that mimics the current YouTube behaviour.

    When produce_subtitles is True it writes an en-orig SRT only for the
    web_embedded fallback invocation (i.e. the default client yields nothing and
    still exits 0).
    """
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    script = fake_dir / "fake_ytdlp.py"
    body = "import pathlib\nimport sys\n\nargs = sys.argv[1:]\n"
    if produce_subtitles:
        body += (
            'if "youtube:player_client=web_embedded" in args:\n'
            '    template = args[args.index("-o") + 1]\n'
            '    if "%(ext)s" in template:\n'
            '        name = template.replace("%(title)s", "Fake Video").replace("%(ext)s", "en-orig.srt")\n'
            '    else:\n'
            '        name = template.replace("%(title)s", "Fake Video") + ".en-orig.srt"\n'
            '    pathlib.Path(name).write_text(\n'
            '        "1\\n00:00:01,000 --> 00:00:03,000\\nHello\\n", encoding="utf-8")\n'
        )
    body += "sys.exit(0)\n"
    _ = script.write_text(body, encoding="utf-8")
    shim = fake_dir / "yt-dlp.cmd"
    _ = shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="ascii")
    env = os.environ.copy()
    env["PATH"] = f"{fake_dir}{os.pathsep}{env['PATH']}"
    return env


def _run_download_script(out_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert _POWERSHELL is not None
    return subprocess.run(
        [
            _POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(_DOWNLOAD_PS1),
            "-Url", "https://example.invalid/watch?v=abc123",
            "-NoProxy",
            "-OutputDir", str(out_dir),
        ],
        cwd=str(out_dir), env=env, capture_output=True, text=True,
    )


@pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell not available")
def test_download_script_falls_back_to_web_embedded_and_repairs_subtitles(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    env = _install_fake_ytdlp(tmp_path, produce_subtitles=True)

    proc = _run_download_script(out_dir, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Repair phase strips the "-orig" suffix, leaving the playable en SRT.
    assert (out_dir / "Fake Video.en.srt").exists(), sorted(p.name for p in out_dir.iterdir())
    assert not (out_dir / "Fake Video.en-orig.srt").exists()
    # The fallback must not bake the video extension into the subtitle name.
    assert not (out_dir / "Fake Video.mp4.en.srt").exists()


@pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell not available")
def test_download_script_exits_3_when_subtitles_unavailable(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    env = _install_fake_ytdlp(tmp_path, produce_subtitles=False)

    proc = _run_download_script(out_dir, env)

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert not list(out_dir.glob("*.srt"))
    assert "No English subtitles" in proc.stdout + proc.stderr


@pytest.mark.parametrize("rc", [3, 0])
def test_worker_fails_job_when_download_has_no_subtitles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int
):
    jobs_root = tmp_path / "root"
    video_dir = tmp_path / "videos"
    video_dir.mkdir()

    monkeypatch.setattr(worker, "_ROOT", jobs_root)
    monkeypatch.setattr(worker, "_video_dir", lambda: video_dir)
    monkeypatch.setattr(worker, "_load_config", lambda: {})
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(worker, "_update_job", lambda job_id, **kw: updates.append(kw))

    job_dir = jobs_root / "jobs" / "job1"
    job_dir.mkdir(parents=True)

    def fake_run(cmd, cwd, label, on_line=None):
        (Path(cwd) / "Clip.mp4").write_bytes(b"video")
        return rc

    monkeypatch.setattr(worker, "_run_subprocess", fake_run)

    assert worker._do_download("job1", "https://youtu.be/abc12345678") is None

    assert (video_dir / "Clip.mp4").exists()
    failed = [u for u in updates if u.get("status") == "failed"]
    assert failed, updates
    assert "字幕" in failed[-1]["error"]
    assert failed[-1]["video_name"] == "Clip"
