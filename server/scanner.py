import os
from dataclasses import dataclass, field


@dataclass
class VideoEntry:
    path: str
    name: str
    subtitle: str | None = None
    has_subtitle: bool = False


_SUBTITLE_EXTS = {".srt", ".ass"}
_VIDEO_EXTS = {".mp4"}


def scan_directory(dir_path: str) -> list[VideoEntry]:
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    files = [f for f in os.listdir(dir_path)
             if os.path.isfile(os.path.join(dir_path, f))]

    mp4_files = sorted(
        [f for f in files
         if os.path.splitext(f)[1].lower() in _VIDEO_EXTS]
    )

    entries = []
    for mp4_file in mp4_files:
        full_path = os.path.join(dir_path, mp4_file)
        base_name = os.path.splitext(mp4_file)[0]
        subtitle = _find_subtitle(files, base_name, dir_path)
        entries.append(VideoEntry(
            path=full_path,
            name=mp4_file,
            subtitle=subtitle,
            has_subtitle=subtitle is not None,
        ))

    return entries


def _find_subtitle(files: list[str], base_name: str, dir_path: str) -> str | None:
    for ext in _SUBTITLE_EXTS:
        for f in sorted(files):
            f_base = os.path.splitext(f)[0]
            f_ext = os.path.splitext(f)[1].lower()
            if f_ext == ext and f_base.startswith(base_name):
                return os.path.join(dir_path, f)
    return None
