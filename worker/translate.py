"""Shared chunk translation function — extracted from run_deepseek.py.

Used by both batch_translate.py (full pipeline) and retry logic (single chunk).
"""

import re
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

    # Build numbered input text for attempt 2 (line-by-line fallback)
    numbered_lines = [f"[{i + 1}] {line}" for i, line in enumerate(input_lines)]
    numbered_text = "\n".join(numbered_lines)

    prompts = [
        # Attempt 1: 整体翻译 + 强制行数约束
        f"【最高优先级：行数必须等于 {input_line_count}】\n\n"
        f"翻译以下英文文本为中文。\n\n"
        f"硬性要求（违反即为失败）：\n"
        f"1. 输出恰好 {input_line_count} 行，一行不多、一行不少\n"
        f"2. 每行中文对应同位置的一行英文\n"
        f"3. 不要在行内使用换行符\n"
        f"4. 不要在输出中添加序号、标记或任何额外内容\n\n"
        f"宁可拆分不自然、宁可每行不是完整句子，也必须保证恰好 {input_line_count} 行。\n\n"
        f"{input_text}",

        # Attempt 2: 逐行编号翻译（回退方案，更可靠）
        f"【逐行翻译模式 — 必须严格遵守格式】\n\n"
        f"下面有 {input_line_count} 行英文，每行以 [行号] 开头。\n"
        f"请逐行翻译为中文，输出格式必须与输入格式完全对应。\n\n"
        f"格式规则（缺一不可）：\n"
        f"- 输出恰好 {input_line_count} 行\n"
        f"- 每行以 [行号] 开头，后面紧跟该行的中文翻译\n"
        f"- 行号从 1 到 {input_line_count}，连续不跳号\n"
        f"- [行号] 和中文之间用一个空格分隔\n"
        f"- 不输出英文原文，只输出 [行号] + 中文\n"
        f"- 不添加任何解释、汇总或其他内容\n\n"
        f"示例（如果输入有3行）：\n"
        f"[1] 你好世界\n"
        f"[2] 这是第二行\n"
        f"[3] 这是第三行\n\n"
        f"现在开始翻译以下 {input_line_count} 行：\n\n"
        f"{numbered_text}",
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

        # Attempt 2: strip [N] prefixes from numbered output
        if attempt == 1:
            raw_lines = result.strip().split("\n")
            stripped = []
            for line in raw_lines:
                line = line.strip()
                m = re.match(r"\[\d+\]\s*(.*)", line)
                if m:
                    stripped.append(m.group(1))
                else:
                    stripped.append(line)
            # Check if numbered format was used; if all lines had prefixes, use stripped version
            if all(re.match(r"\[\d+\]", l.strip()) for l in raw_lines if l.strip()):
                result = "\n".join(stripped)

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
