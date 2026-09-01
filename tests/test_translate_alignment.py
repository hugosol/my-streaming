"""Regression test for the subtitle one-line-shift bug.

English subtitles often split one sentence across several SRT blocks.  The old
translator forced exactly N Chinese lines for N English lines; when a 3-line
English group translated as 2 Chinese lines, the LLM borrowed the next
sentence's translation to fill the third row, shifting every later line up by
one.

The fix translates by sentence group and pads short Chinese groups with blank
rows, so the next group stays in place.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.translate import (
    read_flat_lines,
    split_english_groups,
    translate_chunk,
    write_flat_lines,
)


def _fake_call_skill(*args, **kwargs):
    return (
        "【组1】\n"
        "[1] 我开始想那些比这更有意思的事，\n"
        "[3] 比如报税，或者看油漆变干。\n"
        "\n"
        "【组2】\n"
        "[1] 这真的很可惜，因为现在大部分游戏\n"
        "[2] 教程其实做得相当不错了。\n"
        "\n"
        "【组3】\n"
        "[1] 这是另一个句子。"
    )


def test_translate_chunk_pads_short_chinese_group_with_blank_line():
    """A 3-line English sentence translated as 2 Chinese lines must leave a
    blank row in that group instead of borrowing the next group's text."""
    with tempfile.TemporaryDirectory() as tmp:
        chunk = Path(tmp) / "chunk_001.txt"
        chunk.write_text(
            "I start to think about all the things I\n"
            "could be doing that are more fun than this,\n"
            "like filing my taxes or watching paint dry.\n"
            "And this is a shame, because for the most part video game\n"
            "tutorials are actually pretty good these days.\n"
            "This is another sentence.\n",
            encoding="utf-8",
        )
        output = Path(tmp) / "chunk_001_chinese.txt"

        with patch("worker.skill_caller.call_skill", side_effect=_fake_call_skill):
            ok, err = translate_chunk(chunk, output)

        assert ok, f"expected success, got: {err}"
        lines = read_flat_lines(output.read_text(encoding="utf-8"))
        assert len(lines) == 6, f"expected 6 flat rows, got {len(lines)}: {lines!r}"
        # Anchored placement: the short Chinese group still keeps its first
        # line under English row 1 and its second line under English row 3.
        # The middle row is blank, and the next sentence is not pulled up.
        assert lines[0] == "我开始想那些比这更有意思的事，"
        assert lines[1] == "", "middle row left blank by anchored placement"
        assert lines[2] == "比如报税，或者看油漆变干。"
        assert lines[3] == "这真的很可惜，因为现在大部分游戏"
        assert lines[4] == "教程其实做得相当不错了。"
        assert lines[5] == "这是另一个句子。"


def test_split_english_groups_uses_sentence_final_punctuation():
    lines = [
        "I start to think about all the things I",
        "could be doing that are more fun than this,",
        "like filing my taxes or watching paint dry.",
        "And this is a shame, because for the most part video game",
        "tutorials are actually pretty good these days.",
    ]
    groups = split_english_groups(lines)
    assert [len(g) for g in groups] == [3, 2], groups


def test_flat_line_roundtrip_preserves_trailing_blank():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "flat.txt"
        write_flat_lines(p, ["a", "b", ""])
        assert read_flat_lines(p.read_text(encoding="utf-8")) == ["a", "b", ""]


if __name__ == "__main__":
    tests = [
        test_translate_chunk_pads_short_chinese_group_with_blank_line,
        test_split_english_groups_uses_sentence_final_punctuation,
        test_flat_line_roundtrip_preserves_trailing_blank,
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
    if failures:
        print(f"{failures}/{len(tests)} FAILED")
        sys.exit(1)
    print("All tests passed")
