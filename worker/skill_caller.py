"""DeepSeek API caller that replicates OpenCode skill invocation.

Usage:
    from worker.skill_caller import call_skill

    result = call_skill("srt-punctuator", "Add punctuation to this SRT text...", 
                        system_extra="Only output the punctuated text.")
"""

import json
import os
import re
from pathlib import Path
from openai import OpenAI

_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.json"
_SKILLS_DIR = Path(__file__).parent / "skills"

_config_cache: dict | None = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def _get_api_key() -> str:
    return _load_config().get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")


def build_skill_message(skill_path: Path, args: str = "") -> str:
    """Replicate OpenCode's buildSkillPromptMessage format.
    
    Reads SKILL.md, strips YAML frontmatter (---...---), then formats:
        <body>
        
        ---
        
        Skill: <path>
        User: <args>
    """
    content = skill_path.read_text(encoding="utf-8")
    body = re.sub(r"^---\n[\s\S]*?\n---\n", "", content).strip()
    lines = [body, "", "---", "", f"Skill: {skill_path}"]
    trimmed_args = args.strip()
    if trimmed_args:
        lines.append(f"User: {trimmed_args}")
    return "\n".join(lines)


def call_skill(
    skill_name: str,
    user_message: str,
    system_extra: str = "",
    max_tokens: int = 384000,
    thinking: dict | None = None,
) -> str:
    """Call DeepSeek API mimicking OpenCode skill invocation.
    
    Args:
        skill_name: Skill directory name under worker/skills/ (e.g. "srt-punctuator")
        user_message: The task content / input text
        system_extra: Optional extra instruction appended to the skill message
        max_tokens: Max output tokens
        thinking: Optional per-call override for the configured thinking settings.
            Supports partial dicts, e.g. {"effort": "low"} or {"enabled": False}.
    
    Returns:
        Model response text, or empty string on failure.
    """
    skill_path = _SKILLS_DIR / skill_name / "SKILL.md"
    skill_body = build_skill_message(skill_path, user_message)

    full_content = skill_body
    if system_extra:
        full_content += "\n\n" + system_extra

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("DeepSeek API key not configured (set deepseek_api_key in config.json)")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    config = _load_config()
    model = config.get("model", "deepseek-v4-flash-vision-exp")
    configured_thinking = config.get("thinking", {"enabled": True, "effort": "high"})
    enabled = configured_thinking.get("enabled", True)
    effort = configured_thinking.get("effort", "high")
    if thinking is not None:
        enabled = thinking.get("enabled", enabled)
        effort = thinking.get("effort", effort)

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": full_content}],
        "max_tokens": max_tokens,
        "extra_body": {"thinking": {"type": "enabled" if enabled else "disabled"}},
    }
    if enabled:
        kwargs["reasoning_effort"] = effort

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""
