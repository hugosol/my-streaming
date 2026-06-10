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


def _get_model() -> str:
    """Map config model name to DeepSeek API model name."""
    model_id = _load_config().get("model", "deepseek-v4-flash")
    if "v4-pro" in model_id:
        return "deepseek-reasoner"
    return "deepseek-chat"


def _get_reasoning_effort() -> str:
    """DeepSeek maps: minimal/low/medium -> high, high -> high, xhigh -> max."""
    effort = _load_config().get("reasoning_effort", "medium")
    if effort in ("xhigh", "max"):
        return "max"
    return "high"


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
) -> str:
    """Call DeepSeek API mimicking OpenCode skill invocation.
    
    Args:
        skill_name: Skill directory name under worker/skills/ (e.g. "srt-punctuator")
        user_message: The task content / input text
        system_extra: Optional extra instruction appended to the skill message
        max_tokens: Max output tokens
    
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

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[{"role": "user", "content": full_content}],
        max_tokens=max_tokens,
        extra_body={"thinking": {"type": "enabled"}},
        reasoning_effort=_get_reasoning_effort(),  # type: ignore[arg-type]
    )
    return response.choices[0].message.content or ""
