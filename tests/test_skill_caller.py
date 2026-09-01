"""Tests for worker.skill_caller direct config-driven model/thinking behavior.

Verifies that call_skill passes the configured DeepSeek model name straight to
the API and uses config-driven thinking settings instead of hard-coded mappings.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import worker.skill_caller as skill_caller


class _FakeMessage:
    content: str = "ok"


class _FakeChoice:
    message: Any = _FakeMessage()


class _FakeResponse:
    choices: list[Any] = [_FakeChoice()]


class _FakeCompletions:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_kwargs = kwargs
        return _FakeResponse()


class _FakeChat:
    def __init__(self) -> None:
        self.completions: _FakeCompletions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat: _FakeChat = _FakeChat()


def _write_config(
    tmpdir: str,
    model: str | None = None,
    thinking: dict[str, Any] | None = None,
) -> Path:
    """Write a minimal config and return its path."""
    config: dict[str, Any] = {"deepseek_api_key": "test-key"}
    if model is not None:
        config["model"] = model
    if thinking is not None:
        config["thinking"] = thinking
    config_path = Path(tmpdir) / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _make_skill_dir(tmpdir: str) -> Path:
    """Create a minimal chunk-translator skill for call_skill to read."""
    skill_dir = Path(tmpdir) / "skills" / "chunk-translator"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Test skill body", encoding="utf-8")
    return Path(tmpdir) / "skills"


def _capture_call(
    tmpdir: str,
    model: str | None = None,
    thinking: dict[str, Any] | None = None,
    call_thinking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run call_skill with an isolated config/skill dir and a fake OpenAI client.

    Returns the kwargs that would have been sent to chat.completions.create().
    """
    old_config_path = skill_caller._CONFIG_PATH
    old_skills_dir = skill_caller._SKILLS_DIR
    old_cache = skill_caller._config_cache
    fake_client = _FakeClient()

    try:
        config_path = _write_config(tmpdir, model=model, thinking=thinking)
        skill_caller._CONFIG_PATH = config_path
        skill_caller._SKILLS_DIR = _make_skill_dir(tmpdir)
        skill_caller._config_cache = None

        with patch.object(skill_caller, "OpenAI", return_value=fake_client):
            skill_caller.call_skill(
                "chunk-translator",
                "Translate this line.",
                max_tokens=50,
                thinking=call_thinking,
            )

        create_kwargs = fake_client.chat.completions.create_kwargs
        if create_kwargs is None:
            raise AssertionError("OpenAI chat.completions.create was not called")
        return create_kwargs
    finally:
        skill_caller._CONFIG_PATH = old_config_path
        skill_caller._SKILLS_DIR = old_skills_dir
        skill_caller._config_cache = old_cache


def test_call_skill_uses_config_model_and_thinking():
    """Configured model and thinking settings are sent verbatim."""
    tmpdir = tempfile.mkdtemp()
    try:
        kwargs = _capture_call(
            tmpdir,
            model="deepseek-v4-flash-vision-exp",
            thinking={"enabled": True, "effort": "high"},
        )
        assert kwargs["model"] == "deepseek-v4-flash-vision-exp"
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kwargs["reasoning_effort"] == "high"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_call_skill_disabled_thinking_omits_effort():
    """disabled thinking sends disabled toggle and does not send effort."""
    tmpdir = tempfile.mkdtemp()
    try:
        kwargs = _capture_call(
            tmpdir,
            model="deepseek-v4-flash-vision-exp",
            thinking={"enabled": False, "effort": "low"},
        )
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in kwargs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_call_skill_defaults_when_config_fields_missing():
    """Missing model/thinking falls back to the agreed defaults."""
    tmpdir = tempfile.mkdtemp()
    try:
        kwargs = _capture_call(tmpdir)
        assert kwargs["model"] == "deepseek-v4-flash-vision-exp"
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kwargs["reasoning_effort"] == "high"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_call_skill_does_not_map_model_names():
    """No hard-coded deepseek-chat / deepseek-reasoner mapping remains."""
    tmpdir = tempfile.mkdtemp()
    try:
        for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
            tmpdir_call = tempfile.mkdtemp()
            try:
                kwargs = _capture_call(tmpdir_call, model=model)
                assert kwargs["model"] == model
            finally:
                shutil.rmtree(tmpdir_call, ignore_errors=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_call_skill_thinking_override_low_effort():
    """Per-call low-effort override is sent instead of configured high effort."""
    tmpdir = tempfile.mkdtemp()
    try:
        kwargs = _capture_call(
            tmpdir,
            model="deepseek-v4-flash-vision-exp",
            thinking={"enabled": True, "effort": "high"},
            call_thinking={"effort": "low"},
        )
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kwargs["reasoning_effort"] == "low"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_call_skill_thinking_override_disabled():
    """Per-call disabled override suppresses reasoning_effort."""
    tmpdir = tempfile.mkdtemp()
    try:
        kwargs = _capture_call(
            tmpdir,
            model="deepseek-v4-flash-vision-exp",
            thinking={"enabled": True, "effort": "high"},
            call_thinking={"enabled": False},
        )
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in kwargs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    tests = [
        test_call_skill_uses_config_model_and_thinking,
        test_call_skill_disabled_thinking_omits_effort,
        test_call_skill_defaults_when_config_fields_missing,
        test_call_skill_does_not_map_model_names,
        test_call_skill_thinking_override_low_effort,
        test_call_skill_thinking_override_disabled,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as e:
            print(f"FAIL {test.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR {test.__name__}: {e}")
            failures += 1
    print()
    if failures:
        print(f"{failures}/{len(tests)} FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed")
