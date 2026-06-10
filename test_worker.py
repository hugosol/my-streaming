"""Unit tests for worker URL extraction and cleaning logic."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def clean_url(url: str) -> str:
    """Replicate worker's URL cleaning logic."""
    if "youtube.com/watch?v=" in url and "&" in url:
        url = url.split("&")[0]
    elif "youtube.com/watch?v=" not in url and "?" in url:
        url = url.split("?")[0]
    return url


def extract_video_id(url: str) -> str:
    """Replicate worker's video ID extraction."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


# (input_url, expected_video_id)
TESTS = [
    # shorts
    ("https://youtube.com/shorts/hmWLXgLtO_c?si=CQ45S_qAIED8Suod", "hmWLXgLtO_c"),
    ("https://youtube.com/shorts/hmWLXgLtO_c", "hmWLXgLtO_c"),
    ("https://www.youtube.com/shorts/abc123def45", "abc123def45"),
    # watch
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx", "dQw4w9WgXcQ"),
    # youtu.be
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=xxx", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=30", "dQw4w9WgXcQ"),
    # invalid — should return empty string
    ("https://youtube.com/live/abc123def45", ""),
    ("https://example.com", ""),
    ("not a url", ""),
    ("", ""),
]

if __name__ == "__main__":
    failures = 0
    for url, expected in TESTS:
        cleaned = clean_url(url)
        vid = extract_video_id(cleaned)
        ok = vid == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        # Compact display
        short = url[:70] + "..." if len(url) > 70 else url
        print(f"[{status}] vid={vid or '(none)':14s} input={short}")

    print()
    if failures:
        print(f"{failures}/{len(TESTS)} FAILED")
        sys.exit(1)
    else:
        print(f"All {len(TESTS)} tests passed")
