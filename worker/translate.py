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


# A sentence group ends when an English line ends with sentence-final
# punctuation.  These may be followed by a closing quote/paren.
_SENTENCE_END_RE = re.compile(r'[.!?…][”\'"）)]?$')


def split_english_groups(lines: list[str]) -> list[list[str]]:
    """Split non-empty English lines into sentence groups.

    A new group begins after a line ending with sentence-final punctuation.
    Consecutive lines that do not end a sentence stay in the same group, so a
    sentence split over multiple SRT blocks is translated as one unit.
    """
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        current.append(s)
        if _SENTENCE_END_RE.search(s):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def split_translation_groups(text: str) -> list[list[tuple[int | None, str]]]:
    """Parse LLM translation output into groups of (target_line, text).

    Each Chinese line may carry a [N] anchor referring to the matching English
    line number inside its group.  The anchor is preserved so callers can place
    the translation under the correct English row instead of stacking all
    Chinese lines at the end of the group.

    Also tolerates common LLM decorations: group labels such as 【组1】 and
    runs of dashes/equals used as separators.
    """
    raw_lines = text.split("\n")
    groups: list[list[tuple[int | None, str]]] = []
    current: list[tuple[int | None, str]] = []

    def _clean(line: str) -> tuple[int | None, str]:
        s = line.strip()
        # Remove a standalone/leading group label before looking for [N].
        s = re.sub(
            r'^(?:【|\[)?\s*(?:组|GROUP|Group)\s*\d+\s*[】\]]?\s*[:：]?\s*',
            '', s
        ).strip()
        m = re.match(r'^\[(\d+)\]\s*(.*)$', s)
        if m:
            return int(m.group(1)), m.group(2).strip()
        return None, s

    for line in raw_lines:
        s = line.strip()
        if not s or re.match(r'^[\-=*]{3,}$', s):
            if current:
                groups.append(current)
                current = []
            continue
        target, cleaned = _clean(line)
        if not cleaned:
            continue
        current.append((target, cleaned))

    if current:
        groups.append(current)
    return groups


def write_flat_lines(path: Path, lines: list[str]) -> None:
    """Write one flat line per SRT block, preserving blank placeholder rows.

    A trailing blank row must survive PowerShell's Get-Content, so the file is
    written with one final newline; two newlines at the end represent a real
    trailing blank row.
    """
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_flat_lines(text: str) -> list[str]:
    """Read the flat representation written by write_flat_lines."""
    if text == "":
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _format_grouped_input(groups: list[list[str]]) -> str:
    parts = []
    for i, group in enumerate(groups, 1):
        numbered = "\n".join(f"[{j + 1}] {line}" for j, line in enumerate(group))
        parts.append(f"【组{i}】\n" + numbered)
    return "\n\n".join(parts)


def _build_prompts(input_groups: list[list[str]]) -> list[str]:
    group_count = len(input_groups)
    formatted = _format_grouped_input(input_groups)

    # The full output contract lives in worker/skills/chunk-translator/SKILL.md;
    # call_skill injects it into every request.  Keep this user prompt short and
    # only carry the dynamic input plus one retry-specific framing line.
    base = (
        f"共有 {group_count} 个英文句组。按 SKILL.md 的规则逐组翻译，"
        f"每行中文带 [行号]，组间用空行分隔。\n\n"
        f"{formatted}"
    )

    return [
        # Attempt 1: understand each group as a whole, then place lines.
        f"【自然译法】先理解整组，再尽量逐行落位。\n\n{base}",

        # Attempt 2: maximize line-level correspondence.
        f"【逐行优先】能独立翻译的英文行就独立翻译；只有合并更自然时才减少行数，"
        f"并标出主要对应的行号。\n\n{base}",

        # Attempt 3: strict one-to-one fallback, used only after natural
        # and line-priority attempts fail to satisfy the parser.
        f"【逐行对齐】每一行英文单独翻译成一行中文；行号按 1..n 顺序，"
        f"不合并、不减少。\n\n{base}",
    ]


def _strip_fences_and_preamble(text: str) -> str:
    """Remove markdown code fences and LLM preamble from translation output.

    Defends against LLMs that:
    - Wrap output in ```text ... ```
    - Prefix with a "plan" preamble like "好的，我将严格遵循..."
    - Wrap English original in fences and put Chinese translation outside
      (common failure mode: first fence pair = echoed English, real
      translation follows after the closing fence).

    Strategy: strip fence markers, detect and remove preamble lines,
    then prefer content after the last fence if multiple fence pairs exist.
    """
    lines = text.split("\n")

    # Collect fence line indices (```, ```text, ```language)
    fence_indices = [
        i for i, line in enumerate(lines)
        if re.match(r'^\s*```', line.strip())
    ]

    if fence_indices:
        # If content exists after the last fence, prefer it — the earlier
        # fence pair may be the LLM echoing English original.
        last_close = fence_indices[-1]
        after_last = lines[last_close + 1:]
        # Also collect content between the last open-close pair if balanced
        if len(fence_indices) >= 2:
            # Last pair: second-to-last is likely open, last is close
            penultimate = fence_indices[-2]
            between = lines[penultimate + 1 : last_close]
            # Prefer content after last fence if non-empty, else between last pair
            after_non_empty = [l for l in after_last if l.strip()]
            if after_non_empty:
                lines = after_last
            else:
                lines = between
        else:
            # Single fence marker: discard everything before it
            lines = lines[fence_indices[0] + 1:]

    # Strip preamble lines (common Chinese LLM preamble patterns).
    # Limit to first 2 lines to avoid stripping legitimate translations
    # that happen to start with preamble-like phrases.
    _preamble_re = re.compile(
        r'^(好的[，,、]|以下是|以下为|翻译结果|这是翻译|根据.*翻译|'
        r'我将|现在开始|下面是|以下是对|OK|Here|Let\s|I\s|The\s)'
    )
    _stripped = 0
    while lines and lines[0].strip() and _stripped < 2:
        if _preamble_re.match(lines[0].strip()):
            lines.pop(0)
            _stripped += 1
        else:
            break

    # Strip leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def translate_chunk(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
    """Translate a single chunk via DeepSeek API with sentence-group alignment.

    English lines are grouped into sentence units before prompting.  Each
    English group is translated as one unit; the Chinese output may have fewer
    lines than the English group.  Missing lines are written as blank lines,
    so the next group's translation cannot shift up by one subtitle row.

    Returns (True, "") on success, (False, error_reason) on failure.
    """
    from worker.skill_caller import call_skill

    if not chunk_path.exists():
        msg = f"Chunk not found: {chunk_path}"
        print(f"[TRANSLATE] {msg}", file=sys.stderr)
        return False, msg

    input_text = chunk_path.read_text(encoding="utf-8")
    input_lines = input_text.split("\n")
    input_non_empty = [l for l in input_lines if l.strip()]
    input_line_count = len(input_non_empty)

    # Group the flat extracted subtitle lines into sentence units.
    input_groups = split_english_groups(input_non_empty)
    group_count = len(input_groups)
    if group_count == 0:
        return False, "Empty input chunk"

    prompts = _build_prompts(input_groups)

    max_retries = 3
    start_time = time.time()
    last_error = ""

    for attempt in range(max_retries):
        prompt = prompts[attempt % len(prompts)]
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

        result = _strip_fences_and_preamble(result)

        output_groups = split_translation_groups(result)

        if len(output_groups) != group_count:
            last_error = (
                f"Group mismatch: expected {group_count} sentence groups, "
                f"got {len(output_groups)}"
            )
            print(f"[TRANSLATE] {last_error} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            continue

        # Expand each Chinese group back to one flat line per English SRT
        # block.  Chinese lines carry [行号] anchors so they land under the
        # English row they actually translate.  Missing rows stay blank.
        flat_lines: list[str] = []
        alignment_error = ""
        for gi, (in_group, out_group) in enumerate(zip(input_groups, output_groups), 1):
            n = len(in_group)
            m = len(out_group)
            if m == 0:
                alignment_error = f"Empty translation for group {gi}"
                break
            if m > n:
                alignment_error = (
                    f"Group {gi} has {m} Chinese lines but only {n} English lines; "
                    f"refusing to shift rows"
                )
                break

            slots = [""] * n
            has_anchor = any(target is not None for target, _ in out_group)

            if not has_anchor:
                # Fallback: model gave no anchors; keep output order, fill from
                # the first English row so Chinese is not stacked at the end.
                for i, (_, text) in enumerate(out_group):
                    slots[i] = text
            elif any(target is None for target, _ in out_group):
                alignment_error = (
                    f"Group {gi} mixes anchored and unanchored Chinese lines"
                )
                break
            else:
                for target, text in out_group:
                    if target < 1 or target > n:
                        alignment_error = (
                            f"Group {gi} anchor {target} outside 1..{n}"
                        )
                        break
                    if slots[target - 1]:
                        alignment_error = (
                            f"Group {gi} has duplicate anchor {target}"
                        )
                        break
                    slots[target - 1] = text
                if alignment_error:
                    break

            flat_lines.extend(slots)

        if alignment_error:
            last_error = alignment_error
            print(f"[TRANSLATE] {last_error} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            continue

        # Guard: very low Chinese ratio is usually an English echo.
        _non_empty = [l for l in flat_lines if l.strip()]
        _chinese_chars = sum(1 for c in result if '\u4e00' <= c <= '\u9fff')
        _total_chars = max(len(result.strip()), 1)
        _chinese_ratio = _chinese_chars / _total_chars
        if _chinese_ratio < 0.03 and input_line_count > 5:
            last_error = f"Low Chinese ratio ({_chinese_ratio:.3f}), likely English echo"
            print(f"[TRANSLATE] {last_error} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            continue

        write_flat_lines(output_path, flat_lines)
        elapsed = time.time() - start_time
        extra = f" (retry {attempt})" if attempt > 0 else ""
        print(
            f"[TRANSLATE] OK {chunk_path.name} -> {output_path.name} "
            f"({elapsed:.1f}s, {group_count} groups, {len(flat_lines)} flat lines){extra}",
            file=sys.stderr,
        )
        return True, ""

    print(f"[TRANSLATE] Failed after {max_retries} attempts: {last_error}", file=sys.stderr)
    return False, last_error
