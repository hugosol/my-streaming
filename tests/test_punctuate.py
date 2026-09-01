"""Regression test for punctuation API Empty-response handling.

The real DeepSeek API can return an empty final message when punctuation is
requested with the configured high reasoning effort (observed on a real job:
chunk 3/14 -> Empty response from API after ~4.6 minutes).  Punctuation is a
mechanical transform, so it should use low reasoning first and fall back to
thinking-disabled if the API still returns an empty response.
"""

import importlib.util
import sys
import tempfile
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


if __name__ == "__main__":
    test_punctuate_retries_empty_response_with_lower_effort()
    print("PASS test_punctuate_retries_empty_response_with_lower_effort")
