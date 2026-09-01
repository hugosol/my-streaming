"""Regression tests for punctuation chunk handling.

1. Empty response handling: the real DeepSeek API can return an empty final
   message when punctuation is requested with the configured high reasoning
   effort (observed on a real job: chunk 3/14 -> Empty response from API after
   ~4.6 minutes).  Punctuation is a mechanical transform, so it should use low
   reasoning first and fall back to thinking-disabled if the API still returns
   an empty response.

2. Parallel chunk processing: punctuation chunks are processed concurrently by
   a ThreadPoolExecutor, mirroring the translation stage's threading model.
"""

import importlib.util
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location(
        "worker_pipeline_under_test", str(ROOT / "worker.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_punctuate_retries_empty_response_with_lower_effort():
    """After an empty API response, punctuation must retry and succeed."""
    worker = _load_pipeline_module()
    with tempfile.TemporaryDirectory() as tmp:
        chunk = Path(tmp) / "chunk_000.txt"
        chunk.write_text("<<0>>hello world", encoding="utf-8")
        output = Path(tmp) / "chunk_000_punctuated.txt"

        calls: list[dict] = []

        def fake_call_skill(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return ""
            return "<<0>>Hello, world."

        with patch("worker.skill_caller.call_skill", side_effect=fake_call_skill):
            ok, err = worker._run_punctuate_chunk(chunk, output)

        assert ok, f"expected success after retry, got: {err}"
        assert len(calls) == 2, f"expected two API attempts, got {len(calls)}"
        assert calls[0]["thinking"] == {"effort": "low"}, calls[0]
        assert calls[1]["thinking"] == {"enabled": False}, calls[1]
        assert output.read_text(encoding="utf-8") == "<<0>>Hello, world."


def test_do_punctuate_processes_chunks_in_parallel():
    """Punctuation chunks must be processed concurrently and all finalized."""
    worker = _load_pipeline_module()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        srt_path = root / "test.srt"
        blocks = []
        for i in range(6):
            blocks.append(
                f"{i + 1}\n00:00:0{i},000 --> 00:00:0{i + 1},000\nline {i}"
            )
        srt_path.write_text("\n\n".join(blocks), encoding="utf-8")

        job_id = "punctuate-parallel-test"
        total_chunks = 6

        def fake_run_subprocess(cmd, cwd, label, on_line=None):
            if label == "PUNCT-PREPARE":
                work_dir = root / "test.punc_work"
                chunks_dir = work_dir / "chunks"
                chunks_dir.mkdir(parents=True, exist_ok=True)
                (work_dir / "chunks.json").write_text(
                    json.dumps({"total_chunks": total_chunks}),
                    encoding="utf-8",
                )
                for i in range(total_chunks):
                    (chunks_dir / f"chunk_{i:03d}.txt").write_text(
                        f"<<{i}>>hello world {i}", encoding="utf-8"
                    )
            return 0

        calls = []
        active = 0
        max_active = 0
        lock = threading.Lock()
        two_active = threading.Event()

        def fake_punctuate_chunk(chunk_path, output_path):
            nonlocal active, max_active
            calls.append(chunk_path.name)
            with lock:
                active += 1
                max_active = max(max_active, active)
                if active >= 2:
                    two_active.set()
            two_active.wait(timeout=2)
            time.sleep(0.05)
            output_path.write_text(chunk_path.read_text(), encoding="utf-8")
            with lock:
                active -= 1
            return True, ""

        with (
            patch.object(worker, "_load_config", return_value={
                "punctuation_check": {
                    "expected_per_lines": 0.333,
                    "threshold_factor": 0.4,
                },
                "punctuation": {"thread_num": 2},
            }),
            patch.object(worker, "_update_job", return_value=None),
            patch.object(worker, "_run_subprocess", side_effect=fake_run_subprocess),
            patch.object(worker, "_run_punctuate_chunk", side_effect=fake_punctuate_chunk),
        ):
            ok = worker._do_punctuate(job_id, srt_path)

        assert ok
        assert len(calls) == total_chunks, f"expected {total_chunks} calls, got {len(calls)}"
        assert max_active >= 2, f"expected concurrent punctuation, max_active={max_active}"
        for i in range(total_chunks):
            output_file = root / "test.punc_work" / "chunks" / f"chunk_{i:03d}_punctuated.txt"
            assert output_file.exists(), f"missing {output_file.name}"


if __name__ == "__main__":
    test_punctuate_retries_empty_response_with_lower_effort()
    test_do_punctuate_processes_chunks_in_parallel()
    print("PASS test_punctuate_retries_empty_response_with_lower_effort")
    print("PASS test_do_punctuate_processes_chunks_in_parallel")
