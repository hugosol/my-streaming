#!/usr/bin/env python3
"""Drop-in replacement for run_opencode.py that calls DeepSeek API directly.

Maintains the same CLI interface so batch_translate.py needs no changes.
"""

import argparse
import re
import sys
import time
from pathlib import Path

# Allow running as script from worker/scripts/ directory
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from worker.skill_caller import call_skill


def _extract_skill_and_file(prompt: str) -> tuple[str, str]:
    """Parse prompt like '使用skill: chunk-translator, 翻译文件 /path/to/file.txt'
    Returns (skill_name, file_path).
    """
    skill_match = re.search(r"使用skill:\s*([\w-]+)", prompt)
    skill_name = skill_match.group(1) if skill_match else "chunk-translator"
    
    file_match = re.search(r"翻译文件\s+(.+)", prompt)
    if not file_match:
        file_match = re.search(r"([A-Za-z]:[^\s]+\.\w+)", prompt)
    if not file_match:
        file_match = re.search(r"(\S+\.\w+)", prompt)
    file_path = file_match.group(1).strip() if file_match else ""
    
    return skill_name, file_path


def main():
    parser = argparse.ArgumentParser(description="DeepSeek API skill caller (replaces run_opencode.py)")
    parser.add_argument("--prompt", required=True, help="Skill prompt")
    parser.add_argument("--expected-file", required=True, help="Expected output file path")
    parser.add_argument("--workdir", default=".", help="Working directory")
    parser.add_argument("--log-file", default="", help="Log file path (ignored)")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds")
    parser.add_argument("--log-filter", default="minimal", help="Log filter level (ignored)")
    
    args = parser.parse_args()
    
    skill_name, input_file = _extract_skill_and_file(args.prompt)
    
    input_path = Path(input_file)
    if not input_path.is_absolute():
        input_path = Path(args.workdir) / input_file
    
    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    input_text = input_path.read_text(encoding="utf-8")
    input_lines = input_text.strip().split("\n")
    input_line_count = len(input_lines)

    expected_path = Path(args.expected_file)
    if not expected_path.is_absolute():
        expected_path = Path(args.workdir) / args.expected_file

    # Build prompts with progressive strictness for retries
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
    result = ""
    start_time = time.time()

    for attempt, prompt in enumerate(prompts):
        try:
            result = call_skill(
                skill_name=skill_name,
                user_message=prompt,
                max_tokens=384000,
            )
        except Exception as e:
            print(f"[ERROR] DeepSeek API call failed (attempt {attempt + 1}/{max_retries}): {e}", file=sys.stderr)
            if attempt + 1 >= max_retries:
                sys.exit(1)
            continue

        if not result:
            print(f"[ERROR] Empty response (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt + 1 >= max_retries:
                sys.exit(1)
            continue

        # Validate line count
        output_lines = result.strip().split("\n")
        output_line_count = len(output_lines)

        if output_line_count == input_line_count:
            expected_path.write_text(result, encoding="utf-8")
            elapsed = time.time() - start_time
            extra = f" (retry {attempt})" if attempt > 0 else ""
            print(f"[OK] Translated {input_path.name} -> {expected_path.name} ({elapsed:.1f}s, {input_line_count} lines){extra}")
            sys.exit(0)
        else:
            print(f"[WARN] Line count mismatch: input={input_line_count}, output={output_line_count} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)

    print(f"[ERROR] Failed after {max_retries} attempts: could not match line count", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
