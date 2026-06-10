"""Tests for --skip-translate mode in batch_translate (Slice 04).

Creates pre-made chunk files and verifies batch_translate.py --skip-translate
aggregates them correctly without calling any external API.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _make_mock_workspace(tmpdir: str) -> tuple[Path, Path]:
    """Create a mock workspace with pre-made chunk and _chinese.txt files.

    Returns (txt_path, workspace_dir).
    """
    base = Path(tmpdir)
    # Create a plain text file with enough lines to split into 4 chunks (CHUNK_SIZE=100)
    # Use ~250 lines so it splits into at least 3 chunks
    lines = []
    for i in range(1, 251):
        lines.append(f"This is subtitle line number {i}.")
    txt_content = "\n".join(lines)
    txt_path = base / "test.txt"
    txt_path.write_text(txt_content, encoding="utf-8")

    # batch_translate.py with --no-extract creates workspace: base / "test_workspace"
    workspace_dir = base / "test_workspace"
    workspace_dir.mkdir()
    chunks_dir = workspace_dir / "chunks"
    chunks_dir.mkdir()

    # Create chunk files (must match what batch_translate.py would create)
    # batch_translate.py writes chunks as chunk_001.txt, chunk_002.txt, etc.
    # in the output_dir (workspace_dir/chunks with --output-dir=chunks default)
    # But with --output-dir=<chunks_dir>, it writes directly there.

    # Since we pass --output-dir=<chunks_dir>, chunks will be at chunks_dir/
    # We need to pre-create the chunk .txt files AND _chinese.txt files
    # with matching names that batch_translate.py would generate.

    # batch_translate splits 250 lines into chunks of ~100, so ~3 chunks
    # Let's create 3 chunks matching the expected pattern
    chunk_sizes = [100, 100, 50]  # chunk_001: 1-100, chunk_002: 101-200, chunk_003: 201-250
    offset = 0
    for c, size in enumerate(chunk_sizes):
        chunk_idx = c + 1
        chunk_file = chunks_dir / f"chunk_{chunk_idx:03d}.txt"
        chunk_lines = lines[offset:offset + size]
        chunk_file.write_text("\n".join(chunk_lines), encoding="utf-8")

        chinese_file = chunks_dir / f"chunk_{chunk_idx:03d}_chinese.txt"
        chinese_lines = [f"这是字幕行编号 {offset + i + 1}。" for i in range(size)]
        chinese_file.write_text("\n".join(chinese_lines), encoding="utf-8")
        offset += size

    return txt_path, workspace_dir


def test_skip_translate_aggregates_chunks():
    """--skip-translate reads existing _chinese.txt files and aggregates them."""
    tmpdir = tempfile.mkdtemp(prefix="ts_")
    try:
        txt_path, ws_dir = _make_mock_workspace(tmpdir)
        chunks_dir = ws_dir / "chunks"
        script = Path(__file__).parent.parent / "worker" / "scripts" / "batch_translate.py"

        # Run with --skip-translate --no-combine (just aggregate)
        result = subprocess.run(
            [sys.executable, str(script),
             str(txt_path),
             "--no-extract",
             "--no-combine",
             "--skip-translate",
             f"--output-dir={chunks_dir}"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"Exit {result.returncode}\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

        # Verify aggregated output exists
        aggregated = ws_dir / "test_chinese.txt"
        assert aggregated.exists(), f"Aggregated file not found: {aggregated}"

        # Verify content: 250 lines of Chinese
        content = aggregated.read_text(encoding="utf-8")
        lines = [l for l in content.strip().split("\n") if l.strip()]
        assert len(lines) == 250, f"Expected 250 lines, got {len(lines)}"
        for i, line in enumerate(lines):
            assert f"字幕行编号 {i + 1}" in line, f"Line {i + 1} mismatch: {line[:50]}..."
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [test_skip_translate_aggregates_chunks]
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
