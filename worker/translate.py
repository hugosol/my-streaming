"""Shared chunk translation function — extracted from run_deepseek.py.

Used by both batch_translate.py (full pipeline) and retry logic (single chunk).
"""

import sys
import time
from pathlib import Path

# Allow importing from project root
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def translate_chunk(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
    """Translate a single chunk via DeepSeek API with line-count validation.

    Reads chunk_path (English text), calls chunk-translator skill,
    writes Chinese output to output_path. Retries once on line mismatch.

    Returns (True, "") on success, (False, error_reason) on failure.
    """
    from worker.skill_caller import call_skill

    if not chunk_path.exists():
        msg = f"Chunk not found: {chunk_path}"
        print(f"[TRANSLATE] {msg}", file=sys.stderr)
        return False, msg

    input_text = chunk_path.read_text(encoding="utf-8")
    input_lines = input_text.strip().split("\n")
    input_line_count = len(input_lines)

    prompts = [
        f"翻译以下英文文本为中文，输出纯中文。\n"
        f"重要：输入共 {input_line_count} 行，输出必须恰好 {input_line_count} 行，一行不多一行不少。\n\n"
        f"{input_text}",
        f"上次翻译行数不对。重新翻译以下英文文本为中文，输出纯中文。\n"
        f"严格约束：输入共 {input_line_count} 行，你的输出必须恰好 {input_line_count} 行。"
        f"逐行检查，确保第 i 行中文对应第 i 行英文。宁可拆分不自然也要保证行数正确。\n\n"
        f"{input_text}",
    ]

    max_retries = min(len(prompts), 3)
    start_time = time.time()
    last_error = ""

    for attempt, prompt in enumerate(prompts):
        try:
            result = call_skill(
                skill_name="chunk-translator",
                user_message=prompt,
                max_tokens=384000,
            )
        except Exception as e:
            last_error = str(e)
            print(f"[TRANSLATE] API error (attempt {attempt + 1}/{max_retries}): {last_error}", file=sys.stderr)
            if attempt + 1 >= max_retries:
                return False, f"API error: {last_error}"
            continue

        if not result:
            last_error = "Empty response from API"
            print(f"[TRANSLATE] Empty response (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt + 1 >= max_retries:
                return False, last_error
            continue

        output_lines = result.strip().split("\n")
        output_line_count = len(output_lines)

        if output_line_count == input_line_count:
            output_path.write_text(result, encoding="utf-8")
            elapsed = time.time() - start_time
            extra = f" (retry {attempt})" if attempt > 0 else ""
            print(f"[TRANSLATE] OK {chunk_path.name} -> {output_path.name} ({elapsed:.1f}s, {input_line_count} lines){extra}")
            return True, ""
        else:
            last_error = f"Line mismatch: expected {input_line_count}, got {output_line_count}"
            print(f"[TRANSLATE] {last_error} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)

    print(f"[TRANSLATE] Failed after {max_retries} attempts: {last_error}", file=sys.stderr)
    return False, last_error
