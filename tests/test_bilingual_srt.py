"""Bilingual SRT generation (seam S1).

A real re-segmented SRT plus real Translation Chunk products are written into a
temporary directory; the tests read the Bilingual SRT the module writes back to
disk and compare it with the output the pre-change implementation (the
PowerShell combine script) produced for the same fixture.  That golden text was
captured by running the normal translation path on this fixture before the
combine step moved into the module, so it is an independent source of truth.
"""

import importlib.machinery
import importlib.util
import json
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker.translate as translate_module

_loader = importlib.machinery.SourceFileLoader(
    "bilingual_srt_worker", str(Path(__file__).resolve().parent.parent / "worker.py")
)
_spec = importlib.util.spec_from_loader("bilingual_srt_worker", _loader)
worker = importlib.util.module_from_spec(_spec)
_loader.exec_module(worker)

# The normal translation path's own script, loaded the same way, so its assembly
# step can be driven with the external call the repair was handed.
_batch_loader = importlib.machinery.SourceFileLoader(
    "batch_translate_under_test",
    str(Path(__file__).resolve().parent.parent / "worker" / "scripts" / "batch_translate.py"),
)
_batch_spec = importlib.util.spec_from_loader("batch_translate_under_test", _batch_loader)
batch_translate = importlib.util.module_from_spec(_batch_spec)
_batch_loader.exec_module(batch_translate)

import worker.bilingual_srt as bilingual_srt_module
from worker.bilingual_srt import (
    SentenceGroup,
    SubtitleBlock,
    generate_bilingual_srt,
    parse_timed_english_srt,
    rebuild_sentence_groups,
    validate_block_count,
    validate_english_preserved,
    validate_group_ranges,
    validate_time_legality,
)
from worker.translate import read_flat_lines, split_english_groups, write_flat_lines

SRT_TEXT = """1
00:00:10,000 --> 00:00:12,500
This is one reason why it was so important for Capcom to never

2
00:00:12,500 --> 00:00:15,000
reveal that Resident Evil 4 was

3
00:00:15,000 --> 00:00:18,200
using a dynamic difficulty system.

4
00:00:18,200 --> 00:00:21,000
And this is a shame, because for the most part video game

5
00:00:21,000 --> 00:00:23,400
tutorials are actually pretty good these days.
"""

# Translation Chunk products: one English row per subtitle block, and the
# Chinese rows the translator delivered.  Blocks 2 and 3 carry a Chinese gap,
# exactly like the 203-205 sample in the spec.
ENGLISH_ROWS = [
    "This is one reason why it was so important for Capcom to never",
    "reveal that Resident Evil 4 was",
    "using a dynamic difficulty system.",
    "And this is a shame, because for the most part video game",
    "tutorials are actually pretty good these days.",
]

CHINESE_ROWS = [
    "这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。",
    "",
    "",
    "这真的很可惜，因为现在大部分游戏",
    "教程其实做得相当不错了。",
]

# Output of the pre-change implementation for this fixture: five blocks, each
# followed by its Chinese row (a gap stays a blank row, so a block with no
# Chinese contributes two blank lines) and a blank separator.
EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:10,000 --> 00:00:12,500\n"
    "This is one reason why it was so important for Capcom to never\n"
    "这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。\n"
    "\n"
    "2\n"
    "00:00:12,500 --> 00:00:15,000\n"
    "reveal that Resident Evil 4 was\n"
    "\n"
    "\n"
    "3\n"
    "00:00:15,000 --> 00:00:18,200\n"
    "using a dynamic difficulty system.\n"
    "\n"
    "\n"
    "4\n"
    "00:00:18,200 --> 00:00:21,000\n"
    "And this is a shame, because for the most part video game\n"
    "这真的很可惜，因为现在大部分游戏\n"
    "\n"
    "5\n"
    "00:00:21,000 --> 00:00:23,400\n"
    "tutorials are actually pretty good these days.\n"
    "教程其实做得相当不错了。\n"
    "\n"
)

# The same fixture with no Chinese gap anywhere: every sentence group is fully
# translated, so the repair has nothing to ask for.
GAP_FREE_CHINESE_ROWS = [
    "这就是为什么卡普空绝口不提",
    "《生化危机4》使用了",
    "动态难度系统。",
    "这真的很可惜，因为现在大部分游戏",
    "教程其实做得相当不错了。",
]

GAP_FREE_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:10,000 --> 00:00:12,500\n"
    "This is one reason why it was so important for Capcom to never\n"
    "这就是为什么卡普空绝口不提\n"
    "\n"
    "2\n"
    "00:00:12,500 --> 00:00:15,000\n"
    "reveal that Resident Evil 4 was\n"
    "《生化危机4》使用了\n"
    "\n"
    "3\n"
    "00:00:15,000 --> 00:00:18,200\n"
    "using a dynamic difficulty system.\n"
    "动态难度系统。\n"
    "\n"
    "4\n"
    "00:00:18,200 --> 00:00:21,000\n"
    "And this is a shame, because for the most part video game\n"
    "这真的很可惜，因为现在大部分游戏\n"
    "\n"
    "5\n"
    "00:00:21,000 --> 00:00:23,400\n"
    "tutorials are actually pretty good these days.\n"
    "教程其实做得相当不错了。\n"
    "\n"
)

# Both sentence groups of the fixture carry a Chinese gap (blocks 2 and 4), and
# both live in the same Translation Chunk.
MIXED_CHINESE_ROWS = [
    "这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。",
    "",
    "使用了动态难度系统。",
    "",
    "教程其实做得相当不错了。",
]

# A controlled model answer that re-splits the merged translation of block 1
# across blocks 1 and 2 and leaves block 3 blank: a partial improvement that has
# to be accepted as it stands.
RESPLIT_RESPONSE = (
    "【组1】\n"
    "[1] 这就是为什么卡普空\n"
    "[2] 绝口不提《生化危机4》使用了动态难度系统的原因之一。\n"
    "[3] （空）\n"
)

RESPLIT_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:10,000 --> 00:00:12,500\n"
    "This is one reason why it was so important for Capcom to never\n"
    "这就是为什么卡普空\n"
    "\n"
    "2\n"
    "00:00:12,500 --> 00:00:15,000\n"
    "reveal that Resident Evil 4 was\n"
    "绝口不提《生化危机4》使用了动态难度系统的原因之一。\n"
    "\n"
    "3\n"
    "00:00:15,000 --> 00:00:18,200\n"
    "using a dynamic difficulty system.\n"
    "\n"
    "\n"
    "4\n"
    "00:00:18,200 --> 00:00:21,000\n"
    "And this is a shame, because for the most part video game\n"
    "这真的很可惜，因为现在大部分游戏\n"
    "\n"
    "5\n"
    "00:00:21,000 --> 00:00:23,400\n"
    "tutorials are actually pretty good these days.\n"
    "教程其实做得相当不错了。\n"
    "\n"
)

# Answers that cannot be used: one that only speaks about the first position of
# the group, and one that empties every position.
INCOMPLETE_RESPONSE = "【组1】\n[1] 这就是为什么卡普空\n"
EMPTIED_RESPONSE = "【组1】\n[1] （空）\n[2] （空）\n[3] （空）\n"

# Three sentence groups in three separate Translation Chunks.  The first and the
# last group carry a Chinese gap; the middle one is fully translated.
SPLIT_SRT_TEXT = """1
00:00:01,000 --> 00:00:03,000
The first group opens here

2
00:00:03,000 --> 00:00:05,000
and it closes here.

3
00:00:05,000 --> 00:00:07,000
The second group is already done

4
00:00:07,000 --> 00:00:09,000
and needs no repair.

5
00:00:09,000 --> 00:00:11,000
The third group is not done yet

6
00:00:11,000 --> 00:00:13,000
and still has a gap.
"""

SPLIT_ENGLISH_ROWS = [
    "The first group opens here",
    "and it closes here.",
    "The second group is already done",
    "and needs no repair.",
    "The third group is not done yet",
    "and still has a gap.",
]

SPLIT_CHINESE_ROWS = [
    "第一组从这里开始",
    "",
    "第二组已经完成",
    "不需要修复",
    "第三组还没完成",
    "",
]


def write_chunked_products(
    root: Path,
    name: str,
    srt_text: str,
    english_rows: list[str],
    chinese_rows: list[str],
    chunk_sizes: tuple[int, ...],
) -> Path:
    """Write a real SRT and its real Translation Chunk products under root.

    ``chunk_sizes`` gives how many rows each Translation Chunk holds, so the
    chunk files partition the rows the way the translation stage wrote them.
    """
    srt_path = root / name
    srt_path.write_text(srt_text, encoding="utf-8")

    workspace = root / f"{srt_path.stem}_workspace"
    chunks = workspace / "chunks"
    chunks.mkdir(parents=True)
    start = 0
    for index, size in enumerate(chunk_sizes, 1):
        span = slice(start, start + size)
        write_flat_lines(chunks / f"chunk_{index:03d}.txt", english_rows[span])
        write_flat_lines(chunks / f"chunk_{index:03d}_chinese.txt", chinese_rows[span])
        start += size
    write_flat_lines(workspace / f"{srt_path.stem}_original.txt", english_rows)
    return srt_path


def write_products(
    root: Path, *, chinese_rows: list[str] = CHINESE_ROWS, chunk_sizes: tuple[int, ...] = (5,)
) -> Path:
    """Write the main fixture: one Translation Chunk holding every subtitle block."""
    return write_chunked_products(
        root, "How Games Use Feedback Loops.en.srt", SRT_TEXT, ENGLISH_ROWS, chinese_rows, chunk_sizes
    )


def write_split_products(root: Path) -> Path:
    """Write the three Translation Chunk fixture, one per sentence group."""
    return write_chunked_products(
        root, "Two Gaps.en.srt", SPLIT_SRT_TEXT, SPLIT_ENGLISH_ROWS, SPLIT_CHINESE_ROWS, (2, 2, 2)
    )


def read_products(srt_path: Path) -> tuple[list[str], list[str]]:
    """Read the flat English rows and aggregated Chinese rows back from disk."""
    workspace = srt_path.parent / f"{srt_path.stem}_workspace"
    english = read_flat_lines(
        (workspace / f"{srt_path.stem}_original.txt").read_text(encoding="utf-8")
    )
    chinese: list[str] = []
    for chunk in sorted((workspace / "chunks").glob("chunk_*_chinese.txt")):
        chinese.extend(read_flat_lines(chunk.read_text(encoding="utf-8")))
    return english, chinese


def read_srt_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_generates_bilingual_srt_matching_pre_change_output():
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        model_requests: list[str] = []
        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: model_requests.append(request) or "",
        )

        assert result.ok, result.error
        assert result.path == srt_path.parent / f"Bilingual_{srt_path.name}"
        # The repair asks about the group with the Chinese gap, and an answer it
        # cannot use leaves the artifact exactly as the pre-change implementation
        # produced it.
        assert read_srt_text(result.path) == EXPECTED_BILINGUAL_SRT
        assert len(model_requests) == 1


def test_validate_block_count_requires_one_row_per_block():
    blocks = (
        SubtitleBlock(1, 0, 1000, ("One.",)),
        SubtitleBlock(2, 1000, 2000, ("Two.",)),
        SubtitleBlock(3, 2000, 3000, ("Three.",)),
    )
    assert validate_block_count(blocks, ["一。", "二。", "三。"], ["One.", "Two.", "Three."]) is None
    assert validate_block_count(blocks, ["一。", "二。"]) is not None
    assert validate_block_count(blocks, ["一。", "二。", "三。"] * 2) is not None
    assert validate_block_count(blocks, ["一。", "二。", "三。"], ["One.", "Two."]) is not None


def test_rejects_chinese_rows_that_do_not_match_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        before = srt_path.read_text(encoding="utf-8")
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(srt_path, chinese_rows[:-1], english_rows=english_rows)

        assert not result.ok
        assert "4" in result.error and "5" in result.error, result.error
        assert srt_path.read_text(encoding="utf-8") == before
        assert not (srt_path.parent / f"Bilingual_{srt_path.name}").exists()


def test_rebuilds_sentence_groups_with_the_translation_stage_rule():
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        blocks = parse_timed_english_srt(srt_path.read_text(encoding="utf-8"))

        groups = rebuild_sentence_groups(blocks)

        # The first sentence spans blocks 1-3, the second spans blocks 4-5.
        assert [(g.index, g.block_indices) for g in groups] == [(1, (0, 1, 2)), (2, (3, 4))]
        assert [len(g.block_indices) for g in groups] == [
            len(group) for group in split_english_groups(ENGLISH_ROWS)
        ]


def test_sentence_group_rule_handles_closing_quotes_and_a_trailing_group():
    text = (
        "1\n00:00:00,000 --> 00:00:01,000\nHe said, \"I never\n"
        "\n"
        "2\n00:00:01,000 --> 00:00:02,000\nexpected that.\"\n"
        "\n"
        "3\n00:00:02,000 --> 00:00:03,000\nNo terminator here\n"
    )
    blocks = parse_timed_english_srt(text)

    groups = rebuild_sentence_groups(blocks)

    assert [(g.index, g.block_indices) for g in groups] == [(1, (0, 1)), (2, (2,))]


def test_validate_group_ranges_requires_groups_that_cover_each_block_once():
    assert validate_group_ranges((SentenceGroup(1, (0, 1)), SentenceGroup(2, (2,))), 3) is None
    # a block is skipped
    assert validate_group_ranges((SentenceGroup(1, (0,)), SentenceGroup(2, (2,))), 3) is not None
    # the groups stop before the last block
    assert validate_group_ranges((SentenceGroup(1, (0, 1)),), 3) is not None
    # content moved across a group boundary
    assert validate_group_ranges((SentenceGroup(1, (0, 2)), SentenceGroup(2, (1,))), 3) is not None
    # an empty group
    assert validate_group_ranges((SentenceGroup(1, ()), SentenceGroup(2, (0,))), 1) is not None


def test_validate_time_legality_requires_forward_ordered_blocks():
    good = (SubtitleBlock(1, 0, 1000, ("One.",)), SubtitleBlock(2, 1000, 2500, ("Two.",)))
    assert validate_time_legality(good) is None
    # a block that does not run forward
    assert validate_time_legality((SubtitleBlock(1, 0, 0, ("One.",)),)) is not None
    # a block that starts before the block before it
    assert validate_time_legality(
        (SubtitleBlock(1, 1000, 2000, ("One.",)), SubtitleBlock(2, 500, 900, ("Two.",)))
    ) is not None


def test_rejects_a_subtitle_whose_timecodes_do_not_run_forward():
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "broken.en.srt"
        srt.write_text(
            "1\n00:00:10,000 --> 00:00:12,500\nFirst.\n"
            "\n"
            "2\n00:00:05,000 --> 00:00:06,000\nSecond.\n",
            encoding="utf-8",
        )
        before = srt.read_text(encoding="utf-8")

        result = generate_bilingual_srt(srt, ["一。", "二。"])

        assert not result.ok
        assert "2" in result.error, result.error
        assert srt.read_text(encoding="utf-8") == before
        assert not (srt.parent / f"Bilingual_{srt.name}").exists()


def test_reports_failure_for_a_subtitle_it_cannot_read():
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "unreadable.en.srt"
        srt.write_text("This is not an SRT at all.\n", encoding="utf-8")

        result = generate_bilingual_srt(srt, [])

        assert not result.ok
        assert srt.name in result.error
        assert not (srt.parent / f"Bilingual_{srt.name}").exists()


# --- Local alignment repair: Chinese-only reallocation ---------------------
#
# The repair runs through the same entry point and the injected external call
# dependency.  Every check below reads the model request the dependency received
# and the Bilingual SRT the module actually wrote into the temporary directory.


def test_repair_request_carries_the_triggered_group_and_its_chinese():
    """C2.1: the request holds every English block and the group's own Chinese."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: requests.append(request) or "",
        )

        assert result.ok, result.error
        assert len(requests) == 1, requests
        request = requests[0]
        # the whole triggered group: every English block ...
        assert "[1] This is one reason why it was so important for Capcom to never" in request
        assert "[2] reveal that Resident Evil 4 was" in request
        assert "[3] using a dynamic difficulty system." in request
        # ... and its existing Chinese, the merged translation included
        assert (
            "[1] 这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。"
            in request
        )
        # ... with its Chinese gaps carried as gap positions
        assert "[2] （空）" in request
        assert "[3] （空）" in request
        # the group that has no Chinese gap is not part of the request
        assert "And this is a shame, because for the most part video game" not in request
        assert "tutorials are actually pretty good these days." not in request


def test_repair_is_one_request_per_translation_chunk():
    """C1.2/C2.2: each affected chunk is asked once, other chunks are not asked."""
    with tempfile.TemporaryDirectory() as asked_tmp, tempfile.TemporaryDirectory() as plain_tmp:
        srt_path = write_split_products(Path(asked_tmp))
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: requests.append(request) or "",
        )

        assert result.ok, result.error
        assert len(requests) == 2, requests
        first, last = requests
        assert "The first group opens here" in first
        assert "and it closes here." in first
        assert "The third group is not done yet" not in first
        assert "The third group is not done yet" in last
        assert "and still has a gap." in last
        assert "The first group opens here" not in last
        # the chunk whose groups all have Chinese is never asked about
        assert all("The second group is already done" not in request for request in requests)

        # Nothing was usable, so every block keeps its own rows: the same
        # subtitle the repair-free call delivers.
        reference_srt = write_split_products(Path(plain_tmp))
        reference = generate_bilingual_srt(reference_srt, chinese_rows, english_rows=english_rows)

        assert reference.ok, reference.error
        assert read_srt_text(result.path) == read_srt_text(reference.path)


def test_one_request_carries_every_triggered_group_of_a_chunk():
    """C2.2: two problem groups of one chunk travel in a single request."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as plain_tmp:
        # The fixture keeps both sentence groups in the one Translation Chunk;
        # both of them carry a Chinese gap.
        srt_path = write_products(Path(tmp), chinese_rows=MIXED_CHINESE_ROWS)
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: requests.append(request) or "",
        )

        assert result.ok, result.error
        assert len(requests) == 1, requests
        request = requests[0]
        for english_row in ENGLISH_ROWS:
            assert english_row in request
        # both groups' existing Chinese, and the gap of each of them
        assert "使用了动态难度系统。" in request
        assert "教程其实做得相当不错了。" in request
        assert "[2] （空）" in request
        assert "[1] （空）" in request
        assert "【组1】" in request and "【组2】" in request

        reference_srt = write_products(Path(plain_tmp), chinese_rows=MIXED_CHINESE_ROWS)
        reference = generate_bilingual_srt(reference_srt, chinese_rows, english_rows=english_rows)

        assert reference.ok, reference.error
        assert read_srt_text(result.path) == read_srt_text(reference.path)


def test_accepted_chinese_candidate_is_placed_per_block():
    """C8.1/C6.1: the re-split lands per block; English and times stay put."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: RESPLIT_RESPONSE,
        )

        assert result.ok, result.error
        # The golden text holds five blocks with their input numbering, timecodes
        # and English lines; blocks 1 and 2 carry the re-split halves of the
        # merged translation, block 3 is accepted as still empty, and blocks 4
        # and 5 keep their own Chinese.
        assert read_srt_text(result.path) == RESPLIT_EXPECTED_BILINGUAL_SRT


def test_a_group_cut_by_a_chunk_boundary_is_asked_once_with_all_its_blocks():
    """C2.2: a chunk boundary inside a group neither splits nor repeats the group."""
    with tempfile.TemporaryDirectory() as tmp:
        # Chunk 1 holds blocks 1-2 and chunk 2 holds blocks 3-5, so the first
        # sentence group (blocks 1-3) is cut by the boundary.
        srt_path = write_products(Path(tmp), chinese_rows=MIXED_CHINESE_ROWS, chunk_sizes=(2, 3))
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: requests.append(request) or "",
        )

        assert result.ok, result.error
        assert len(requests) == 2, requests
        chunk_one, chunk_two = requests
        # the cut group travels whole, with the block that lives in the next chunk
        assert "This is one reason why it was so important for Capcom to never" in chunk_one
        assert "using a dynamic difficulty system." in chunk_one
        assert "And this is a shame, because for the most part video game" not in chunk_one
        # and it is not asked about a second time by the chunk it reaches into
        assert "And this is a shame, because for the most part video game" in chunk_two
        assert "using a dynamic difficulty system." not in chunk_two
        assert "This is one reason why it was so important for Capcom to never" not in chunk_two


def test_unusable_candidate_keeps_the_whole_group():
    """C8.2: an answer that cannot be used leaves the group exactly as it was."""
    for answer in (INCOMPLETE_RESPONSE, EMPTIED_RESPONSE):
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = write_products(Path(tmp))
            english_rows, chinese_rows = read_products(srt_path)
            requests: list[str] = []

            result = generate_bilingual_srt(
                srt_path,
                chinese_rows,
                english_rows=english_rows,
                model_call=lambda request, answer=answer: requests.append(request) or answer,
            )

            assert result.ok, result.error
            assert len(requests) == 1, requests
            # The group keeps its merged translation in block 1 and its two empty
            # rows: nothing is invented to fill the gaps, nothing is repeated and
            # nothing is dropped.
            assert read_srt_text(result.path) == EXPECTED_BILINGUAL_SRT


def test_no_repair_request_when_every_group_has_chinese():
    """C1.1: a subtitle without a Chinese gap is delivered untouched and unasked."""
    # A usable answer for both groups: were any of them submitted, its text would
    # reach the artifact.
    unused_answer = (
        "【组1】\n"
        "[1] 不该出现的第一行\n"
        "[2] 不该出现的第二行\n"
        "[3] 不该出现的第三行\n"
        "【组2】\n"
        "[1] 不该出现的第四行\n"
        "[2] 不该出现的第五行\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp), chinese_rows=GAP_FREE_CHINESE_ROWS)
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: requests.append(request) or unused_answer,
        )

        assert result.ok, result.error
        assert requests == []
        assert read_srt_text(result.path) == GAP_FREE_EXPECTED_BILINGUAL_SRT


def test_english_content_preservation_is_not_fooled_by_whitespace():
    # A moved block boundary only changes the whitespace between the words.
    assert (
        validate_english_preserved(
            ["never reveal", "that Resident Evil 4 was"],
            ["never", "reveal that Resident Evil 4 was"],
        )
        is None
    )
    # Same number of words, one word rewritten.
    assert (
        validate_english_preserved(
            ["reveal that Resident Evil 4 was"], ["reveal that Resident Evil 4 is"]
        )
        is not None
    )
    # Same words, changed punctuation.
    assert (
        validate_english_preserved(
            ["using a dynamic difficulty system."], ["using a dynamic difficulty system"]
        )
        is not None
    )
    # Same words, different order.
    assert validate_english_preserved(["never reveal"], ["reveal never"]) is not None
    # Same words, one dropped.
    assert validate_english_preserved(["never reveal that"], ["never that"]) is not None


def test_rejects_english_product_that_disagrees_with_the_subtitle():
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        workspace = srt_path.parent / f"{srt_path.stem}_workspace"
        product = workspace / f"{srt_path.stem}_original.txt"
        write_flat_lines(
            product, [*ENGLISH_ROWS[:1], "reveal that Resident Evil 4 is", *ENGLISH_ROWS[2:]]
        )
        before = srt_path.read_text(encoding="utf-8")
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(srt_path, chinese_rows, english_rows=english_rows)

        assert not result.ok
        assert "English" in result.error, result.error
        assert srt_path.read_text(encoding="utf-8") == before
        assert not (srt_path.parent / f"Bilingual_{srt_path.name}").exists()


@dataclass(frozen=True)
class PathRun:
    """What one real call site delivered: the artifact and the repair's requests."""

    artifact: str
    repair_requests: list[str]
    finished: bool


def _close_batch_logger() -> None:
    """Drop the handlers the batch script adds on every call."""
    logger = logging.getLogger("batch_translate")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def _record_s1_delivery(module, s1_artifacts: list[str]):
    """Wrap a call site's use of the module so a test sees what it wrote."""
    real = generate_bilingual_srt

    def delivered(*args, **kwargs):
        result = real(*args, **kwargs)
        s1_artifacts.append(read_srt_text(result.path) if result.ok and result.path else "")
        return result

    return patch.object(module, "generate_bilingual_srt", side_effect=delivered)


def _assemble_with_batch_script(
    srt_path: Path, model_call
) -> tuple[list[str], list[str]]:
    """Run the normal path's own assembly over the products on disk.

    Runs ``worker/scripts/batch_translate.py`` in process, so the external call its
    assembly step hands to the repair can be controlled.  Returns every request that
    call received, and the Bilingual SRT the module delivered.
    """
    chunks = srt_path.parent / f"{srt_path.stem}_workspace" / "chunks"
    script = Path(__file__).resolve().parent.parent / "worker" / "scripts" / "batch_translate.py"
    argv = [
        str(script),
        str(srt_path),
        "--no-extract",
        "--skip-translate",
        f"--output-dir={chunks}",
    ]
    requests: list[str] = []
    s1_artifacts: list[str] = []

    def call(request: str) -> str:
        requests.append(request)
        return model_call(request)

    previous_cwd = Path.cwd()
    os.chdir(srt_path.parent)
    try:
        with patch.object(sys, "argv", argv), patch.object(
            batch_translate, "repair_alignment_call", call
        ), _record_s1_delivery(batch_translate, s1_artifacts):
            batch_translate.main()
    finally:
        os.chdir(previous_cwd)
        # The script logs into the workspace it is about to have deleted.
        _close_batch_logger()
    return requests, s1_artifacts


def run_normal_path(tmp_root: Path, model_call) -> PathRun:
    """Drive the normal translation path's assembly over the real products.

    Runs the batch translate script itself, in process, so the external call its
    assembly step hands to the repair can be controlled the way the retry path's
    is; ``finished`` says the script treated the run as a success and returned
    instead of exiting.
    """
    srt_path = write_products(tmp_root)
    requests, _ = _assemble_with_batch_script(srt_path, model_call)

    return PathRun(
        read_srt_text(srt_path.parent / f"Bilingual_{srt_path.name}"), requests, True
    )


def run_retry_path(tmp_root: Path, model_call, retranslate=None) -> PathRun:
    """Drive the retry/continue path's assembly over the same real products."""
    config = tmp_root / "config.json"
    config.write_text(
        json.dumps({"db_path": str(tmp_root / "jobs.db"), "video_dir": str(tmp_root)}),
        encoding="utf-8",
    )
    previous = (worker._conn, worker._config, worker._CONFIG_PATH, worker._ROOT)
    worker._conn = None
    worker._config = {}
    worker._CONFIG_PATH = config
    worker._ROOT = tmp_root
    try:
        job_id = worker._create_job("https://youtube.com/watch?v=feedback001", "feedback001")
        job_dir = tmp_root / "jobs" / job_id
        srt_path = job_dir / "How Games Use Feedback Loops.en.srt"
        srt_path.parent.mkdir(parents=True)
        srt_path.write_text(SRT_TEXT, encoding="utf-8")

        workspace = job_dir / f"{srt_path.stem}_workspace"
        chunks = workspace / "chunks"
        chunks.mkdir(parents=True)
        # Chunk 1 already has its Chinese rows; chunk 2 still has to be redone.
        write_flat_lines(chunks / "chunk_001.txt", ENGLISH_ROWS[:2])
        write_flat_lines(chunks / "chunk_001_chinese.txt", CHINESE_ROWS[:2])
        write_flat_lines(chunks / "chunk_002.txt", ENGLISH_ROWS[2:])
        write_flat_lines(workspace / f"{srt_path.stem}_original.txt", ENGLISH_ROWS)
        worker._update_job(
            job_id, status="failed", stage="translating", progress="1/2", error="翻译失败"
        )

        if retranslate is None:

            def retranslate(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
                assert Path(chunk_path).name == "chunk_002.txt"
                write_flat_lines(Path(output_path), CHINESE_ROWS[2:])
                return True, ""

        requests: list[str] = []

        def call(request: str) -> str:
            requests.append(request)
            return model_call(request)

        with patch.object(translate_module, "translate_chunk", side_effect=retranslate), patch.object(
            worker, "_do_finalize", return_value=True
        ) as finalize, patch.object(
            translate_module, "repair_alignment_call", side_effect=call
        ):
            worker._do_retry(job_id)

        return PathRun(
            read_srt_text(srt_path.parent / f"Bilingual_{srt_path.name}"),
            requests,
            bool(finalize.call_count),
        )
    finally:
        if worker._conn is not None:
            worker._conn.close()
        worker._conn, worker._config, worker._CONFIG_PATH, worker._ROOT = previous


def no_answer(request: str) -> str:
    """A controlled external call that answers nothing."""
    return ""


def failing_answer(request: str) -> str:
    """A controlled external call that cannot answer."""
    raise RuntimeError("repair request failed")


def test_normal_and_retry_paths_deliver_the_same_bilingual_srt():
    with tempfile.TemporaryDirectory() as normal_tmp, tempfile.TemporaryDirectory() as retry_tmp:
        normal = run_normal_path(Path(normal_tmp), no_answer)
        retry = run_retry_path(Path(retry_tmp), no_answer)

    assert normal.artifact == EXPECTED_BILINGUAL_SRT
    assert retry.artifact == normal.artifact
    assert normal.finished and retry.finished


def test_both_paths_ask_the_repair_once_and_apply_its_answer():
    """C10.1/C12.1: the repair the two call sites hand their artifact runs on both
    of them — one request for the affected Translation Chunk — and its answer
    reaches the delivered subtitle."""
    with tempfile.TemporaryDirectory() as normal_tmp, tempfile.TemporaryDirectory() as retry_tmp:
        normal = run_normal_path(Path(normal_tmp), lambda request: RESPLIT_RESPONSE)
        retry = run_retry_path(Path(retry_tmp), lambda request: RESPLIT_RESPONSE)

    assert normal.artifact == RESPLIT_EXPECTED_BILINGUAL_SRT
    assert retry.artifact == normal.artifact
    assert len(normal.repair_requests) == 1, normal.repair_requests
    assert len(retry.repair_requests) == 1, retry.repair_requests
    assert normal.finished and retry.finished


def test_a_failed_repair_still_finishes_both_paths_with_the_translated_subtitle():
    """Story 23/C9.3/C10.2: a translation that would have succeeded still succeeds
    when the repair call fails, delivering the rows the chunks already produced."""
    with tempfile.TemporaryDirectory() as normal_tmp, tempfile.TemporaryDirectory() as retry_tmp:
        normal = run_normal_path(Path(normal_tmp), failing_answer)
        retry = run_retry_path(Path(retry_tmp), failing_answer)

    assert normal.artifact == EXPECTED_BILINGUAL_SRT
    assert retry.artifact == EXPECTED_BILINGUAL_SRT
    assert len(normal.repair_requests) == 1, normal.repair_requests
    assert len(retry.repair_requests) == 1, retry.repair_requests
    assert normal.finished and retry.finished


# --- Local alignment repair: moving an English split point inside a group ----
#
# The repair may also move where a sentence group's English is cut.  The words,
# their order and their punctuation stay untouched, the block count stays, and
# the times of the new partition are estimated by the program from the group's
# pre-repair character positions and timecodes.
#
# The fixture's numbers are small enough for the new boundaries to be worked out
# by hand.  "move this word" runs 2,000-4,000 ms over 14 characters, so its
# character j is at 2000 + int(j / 14 * 2000), and the character after it is
# pinned to the block's end (4,000).  "into the next block" runs 4,000-7,000 ms
# over 19 characters; "if it fits there." runs 7,000-11,000 ms over 16 characters.
#
# C7.1 sample — the candidate moves "word" down so that row 1 is "move this" and
# row 2 is "word into the next block", inside the group's text
#
#   "move this word into the next block if it fits there."
#    0123456789...
#
# Row 1 keeps the characters of "move this" only: index 0 up to (and excluding)
# the space at index 9.  Row 2 starts on "w" of "word" at index 10 and still ends
# on the last character of the block that held it, which is pinned to 7,000.
#
#   row 1: start = 2000 + int(0 / 14 * 2000)  = 2000
#          end   = 2000 + int(9 / 14 * 2000)  = 2000 + int(1285.71) = 3285
#   row 2: start = 2000 + int(10 / 14 * 2000) = 2000 + int(1428.57) = 3428
#          end   = the end of "into the next block"               = 7000
#
# Row 3 keeps its own words, so it keeps its own character span and its own
# timecodes (7,000-11,000); the group still starts at 2,000 and ends at 11,000,
# and "done." — a sentence with no Chinese gap — is never touched.

SPLIT_MOVE_SRT_TEXT = """1
00:00:02,000 --> 00:00:04,000
move this word

2
00:00:04,000 --> 00:00:07,000
into the next block

3
00:00:07,000 --> 00:00:11,000
if it fits there.

4
00:00:13,000 --> 00:00:14,000
done.
"""

SPLIT_MOVE_ENGLISH_ROWS = [
    "move this word",
    "into the next block",
    "if it fits there.",
    "done.",
]

# The first sentence group carries a Chinese gap at "into the next block".
SPLIT_MOVE_CHINESE_ROWS = ["把这个词", "", "如果放得下", "完成。"]

MOVED_SPLIT_RESPONSE = (
    "【组1】\n"
    "英文：\n"
    "[1] move this\n"
    "[2] word into the next block\n"
    "[3] if it fits there.\n"
    "中文：\n"
    "[1] 把这个\n"
    "[2] 词移到下一块\n"
    "[3] 如果放得下\n"
)

MOVED_SPLIT_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:02,000 --> 00:00:03,285\n"
    "move this\n"
    "把这个\n"
    "\n"
    "2\n"
    "00:00:03,428 --> 00:00:07,000\n"
    "word into the next block\n"
    "词移到下一块\n"
    "\n"
    "3\n"
    "00:00:07,000 --> 00:00:11,000\n"
    "if it fits there.\n"
    "如果放得下\n"
    "\n"
    "4\n"
    "00:00:13,000 --> 00:00:14,000\n"
    "done.\n"
    "完成。\n"
    "\n"
)

# The same fixture as the subtitle itself has it: what every candidate that
# cannot be used has to leave behind, Chinese rows included.
UNMOVED_SPLIT_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:02,000 --> 00:00:04,000\n"
    "move this word\n"
    "把这个词\n"
    "\n"
    "2\n"
    "00:00:04,000 --> 00:00:07,000\n"
    "into the next block\n"
    "\n"
    "\n"
    "3\n"
    "00:00:07,000 --> 00:00:11,000\n"
    "if it fits there.\n"
    "如果放得下\n"
    "\n"
    "4\n"
    "00:00:13,000 --> 00:00:14,000\n"
    "done.\n"
    "完成。\n"
    "\n"
)


def write_split_move_products(root: Path) -> Path:
    """Write the fixture whose first sentence group may have its split moved."""
    return write_chunked_products(
        root,
        "Move The Split.en.srt",
        SPLIT_MOVE_SRT_TEXT,
        SPLIT_MOVE_ENGLISH_ROWS,
        SPLIT_MOVE_CHINESE_ROWS,
        (4,),
    )


def read_artifact_rows(path: Path) -> list[tuple[str, str]]:
    """The artifact's English row and Chinese row per subtitle block."""
    blocks: list[tuple[str, str]] = []
    for chunk in read_srt_text(path).strip().split("\n\n"):
        lines = chunk.split("\n")
        blocks.append(("\n".join(lines[2:-1]), lines[-1]))
    return blocks


def test_moved_english_split_lands_its_new_english_and_estimated_times():
    """C4.1/C5.1/C7.1/C7.2/C7.3: the moved split and the times the program
    estimates for it reach the artifact together."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_split_move_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: MOVED_SPLIT_RESPONSE,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == MOVED_SPLIT_EXPECTED_BILINGUAL_SRT
        # C4.1: the group's English is every original word, in order, with its
        # punctuation — only cut at another place.
        artifact = read_artifact_rows(result.path)
        assert " ".join(row for row, _ in artifact[:3]).split() == " ".join(
            SPLIT_MOVE_ENGLISH_ROWS[:3]
        ).split()
        assert all(row.strip() for row, _ in artifact)


def test_timecodes_a_candidate_writes_never_reach_the_artifact():
    """C7.3: the times are computed from the pre-repair positions, not read from
    the candidate that suggests the new split."""
    answer = (
        "【组1】\n"
        "英文：\n"
        "[1] 00:00:02,100 --> 00:00:04,400 move this\n"
        "[2] 00:00:04,400 --> 00:00:04,900 word into the next block\n"
        "[3] 00:00:05,000 --> 00:00:05,100 if it fits there.\n"
        "中文：\n"
        "[1] 把这个\n"
        "[2] 词移到下一块\n"
        "[3] 如果放得下\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_split_move_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        artifact = read_srt_text(result.path)
        assert artifact == MOVED_SPLIT_EXPECTED_BILINGUAL_SRT
        for claimed in ("00:00:02,100", "00:00:04,400", "00:00:04,900", "00:00:05,100"):
            assert claimed not in artifact


def test_an_english_answer_that_keeps_the_original_split_keeps_every_timecode():
    """P6/C6.1: an answer that gives the group's English back as it already is cut
    reallocates the Chinese and leaves every timecode alone."""
    answer = (
        "【组1】\n"
        "英文：\n"
        "[1] move this word\n"
        "[2] into the next block\n"
        "[3] if it fits there.\n"
        "中文：\n"
        "[1] 把这个\n"
        "[2] 词移到下一块\n"
        "[3] 如果放得下\n"
    )
    expected = (
        "1\n"
        "00:00:02,000 --> 00:00:04,000\n"
        "move this word\n"
        "把这个\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:07,000\n"
        "into the next block\n"
        "词移到下一块\n"
        "\n"
        "3\n"
        "00:00:07,000 --> 00:00:11,000\n"
        "if it fits there.\n"
        "如果放得下\n"
        "\n"
        "4\n"
        "00:00:13,000 --> 00:00:14,000\n"
        "done.\n"
        "完成。\n"
        "\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_split_move_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == expected


def build_repair_answer(english_rows: list[str] | None, chinese_rows: list[str]) -> str:
    """One group's answer, as a model would write it."""
    lines = ["【组1】"]
    if english_rows is not None:
        lines.append("英文：")
        lines.extend(f"[{position}] {row}" for position, row in enumerate(english_rows, 1))
    lines.append("中文：")
    lines.extend(f"[{position}] {row}" for position, row in enumerate(chinese_rows, 1))
    return "\n".join(lines) + "\n"


# A moved split whose words are not the subtitle's words any more, or whose rows
# do not describe the group's three blocks.  Every one of them comes with a
# usable Chinese part, so nothing but the English can make the group fall back.
UNUSABLE_ENGLISH_MOVES = {
    "changed word": ["move this", "word into the next blocks", "if it fits there."],
    "dropped word": ["move this", "word into next block", "if it fits there."],
    "duplicated word": ["move this this", "word into the next block", "if it fits there."],
    "reordered words": ["this move", "word into the next block", "if it fits there."],
    "changed punctuation": ["move this", "word into the next block!", "if it fits there."],
    "content of another sentence": [
        "move this",
        "word into the next block done.",
        "if it fits there.",
    ],
    "fewer rows than blocks": ["move this", "word into the next block"],
    "an empty row": ["move this word into the next block", "", "if it fits there."],
}

GOOD_CHINESE_MOVE = ["把这个", "词移到下一块", "如果放得下"]


def test_unusable_english_moves_leave_the_whole_group_alone():
    """C4.2/C5.1/C5.2: an English move that is not the subtitle's own words, or
    that does not describe the group's blocks, cannot enter the artifact — and it
    takes its Chinese reallocation down with it."""
    for reason, english in UNUSABLE_ENGLISH_MOVES.items():
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = write_split_move_products(Path(tmp))
            english_rows, chinese_rows = read_products(srt_path)

            result = generate_bilingual_srt(
                srt_path,
                chinese_rows,
                english_rows=english_rows,
                model_call=lambda request, answer=build_repair_answer(
                    english, GOOD_CHINESE_MOVE
                ): answer,
            )

            assert result.ok, f"{reason}: {result.error}"
            assert read_srt_text(result.path) == UNMOVED_SPLIT_EXPECTED_BILINGUAL_SRT, reason


def test_a_moved_split_without_a_usable_chinese_part_is_not_half_applied():
    """C9.1/C11.2: the English move and the Chinese it comes with are one answer;
    a group that cannot be answered completely keeps its own English and time."""
    answer = build_repair_answer(
        ["move this", "word into the next block", "if it fits there."], ["把这个"]
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_split_move_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == UNMOVED_SPLIT_EXPECTED_BILINGUAL_SRT


# "we must go" is shown for 4 ms over 10 characters, so its character positions
# share milliseconds: 1000 + int(j / 10 * 4) is 1000 for j = 0..2, 1001 for
# j = 3..5, 1002 for j = 6..7 and 1003 for j = 8..9.  A row that holds only "we"
# would run from 1000 to 1000 — a block with no duration, which no subtitle may
# carry, so that candidate has to be refused as a whole.
TIGHT_SRT_TEXT = """1
00:00:01,000 --> 00:00:01,004
we must go

2
00:00:01,004 --> 00:00:03,000
home now
"""

TIGHT_ENGLISH_ROWS = ["we must go", "home now"]

TIGHT_CHINESE_ROWS = ["我们", ""]

TIGHT_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:01,004\n"
    "we must go\n"
    "我们\n"
    "\n"
    "2\n"
    "00:00:01,004 --> 00:00:03,000\n"
    "home now\n"
    "\n"
    "\n"
)


def write_tight_products(root: Path) -> Path:
    """Write the fixture whose blocks are too short for some of their own cuts."""
    return write_chunked_products(
        root,
        "Tight Times.en.srt",
        TIGHT_SRT_TEXT,
        TIGHT_ENGLISH_ROWS,
        TIGHT_CHINESE_ROWS,
        (2,),
    )


def test_a_moved_split_that_cannot_be_given_legal_times_leaves_the_group_alone():
    """C7.2: a candidate whose estimated times cannot keep every block running
    forward is not used at all, so no broken or overlapping axis is delivered."""
    answer = build_repair_answer(["we", "must go home now"], ["我们", "必须走了"])
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_tight_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == TIGHT_EXPECTED_BILINGUAL_SRT


# The same sentence, in a subtitle whose timecodes have already been adjusted by
# earlier processing: "move this" runs 2,000-2,900 ms over 9 characters and
# "word into the next block" 3,100-7,000 ms.  Hand computation of what moving the
# split down has to produce, from those timecodes and nothing else:
#
#   char_ms[j] = 2000 + int(j / 9 * 900) = 2000 + 100j for j = 0..8
#   row "move"          covers index 0..3, so its end is index 4  -> 2000 + 400 = 2400
#   row "this word ..." starts on "t" of "this" at index 5        -> 2000 + 500 = 2500
#   row "this word ..." ends on the last character of its block    -> 7000
#   row "if it fits there." keeps its own span                    -> 7000..11000
#
# An estimate taken from any other origin — the times the subtitle had before it
# was adjusted, or the ones an earlier estimate produced — would land elsewhere,
# so these numbers pin the basis to the pre-repair timecodes of this call.
ADJUSTED_SRT_TEXT = """1
00:00:02,000 --> 00:00:02,900
move this

2
00:00:03,100 --> 00:00:07,000
word into the next block

3
00:00:07,000 --> 00:00:11,000
if it fits there.
"""

ADJUSTED_ENGLISH_ROWS = ["move this", "word into the next block", "if it fits there."]

ADJUSTED_CHINESE_ROWS = ["把这个", "", "如果放得下"]

ADJUSTED_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:02,000 --> 00:00:02,400\n"
    "move\n"
    "移\n"
    "\n"
    "2\n"
    "00:00:02,500 --> 00:00:07,000\n"
    "this word into the next block\n"
    "这个词到下一块\n"
    "\n"
    "3\n"
    "00:00:07,000 --> 00:00:11,000\n"
    "if it fits there.\n"
    "如果放得下\n"
    "\n"
)


def write_adjusted_products(root: Path) -> Path:
    """Write the fixture whose subtitle already carries adjusted timecodes."""
    return write_chunked_products(
        root,
        "Adjusted Times.en.srt",
        ADJUSTED_SRT_TEXT,
        ADJUSTED_ENGLISH_ROWS,
        ADJUSTED_CHINESE_ROWS,
        (3,),
    )


def test_the_estimate_is_based_on_the_timecodes_the_call_received():
    """C7.3: the estimate is read off the pre-repair positions and timecodes of
    the subtitle at hand, so earlier processing cannot bias or accumulate it."""
    answer = build_repair_answer(
        ["move", "this word into the next block", "if it fits there."],
        ["移", "这个词到下一块", "如果放得下"],
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_adjusted_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == ADJUSTED_EXPECTED_BILINGUAL_SRT


# A sentence whose first block already holds a full stop inside it: "he said
# hello. and" only ends the group at "for good.", so the three blocks are one
# sentence group.  A re-partition that ends a row on "hello." would cut the
# subtitle's own sentence in two, which is not a moved split point inside the
# group any more.
SENTENCE_END_SRT_TEXT = """1
00:00:01,000 --> 00:00:02,000
he said hello. and

2
00:00:02,000 --> 00:00:03,000
then he left

3
00:00:03,000 --> 00:00:04,000
for good.
"""

SENTENCE_END_ENGLISH_ROWS = ["he said hello. and", "then he left", "for good."]

SENTENCE_END_CHINESE_ROWS = ["他说了声你好。", "", "就永远离开了。"]

SENTENCE_END_EXPECTED_BILINGUAL_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "he said hello. and\n"
    "他说了声你好。\n"
    "\n"
    "2\n"
    "00:00:02,000 --> 00:00:03,000\n"
    "then he left\n"
    "\n"
    "\n"
    "3\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "for good.\n"
    "就永远离开了。\n"
    "\n"
)


def write_sentence_end_products(root: Path) -> Path:
    """Write the fixture whose group may not be re-cut at its own full stop."""
    return write_chunked_products(
        root,
        "Sentence End.en.srt",
        SENTENCE_END_SRT_TEXT,
        SENTENCE_END_ENGLISH_ROWS,
        SENTENCE_END_CHINESE_ROWS,
        (3,),
    )


def test_a_moved_split_may_not_re_cut_the_subtitles_sentence_groups():
    """C5.1/C5.2: moving a split point keeps the sentence groups the translator
    worked on, so a candidate that ends a group's block on a sentence end is not
    used and the group stays as it is."""
    answer = build_repair_answer(
        ["he said hello.", "and then he left", "for good."],
        ["他说了声你好。", "然后他就走了", "就永远离开了。"],
    )
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_sentence_end_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=lambda request: answer,
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == SENTENCE_END_EXPECTED_BILINGUAL_SRT


# --- Repair failure and request economics -----------------------------------
#
# The local alignment repair is an optional improvement.  A group it cannot
# answer — or a request that cannot be reached at all — keeps the English,
# Chinese and timecodes it already had, is never asked a second time, and never
# turns a usable translation into a failed run.  What the subtitle input itself
# or the artifact write fails on is still a failure, and is never passed off as
# a repair that rolled itself back.


def controlled_repair(requests: list[str], answer: str | None):
    """A controlled external call that records every request and then answers it.

    ``None`` stands for a request that fails outright.
    """

    def call(request: str) -> str:
        requests.append(request)
        if answer is None:
            raise RuntimeError("repair request failed")
        return answer

    return call


def test_a_repair_request_that_fails_keeps_the_original_result():
    """C9.2/C9.3/C10.2: a request that fails leaves the affected request scope
    with the result it already had, and is not asked again."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)
        before = srt_path.read_text(encoding="utf-8")
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=controlled_repair(requests, None),
        )

        # the run still succeeds ...
        assert result.ok, result.error
        # ... after exactly one repair request, not a second attempt ...
        assert len(requests) == 1, requests
        # ... and the artifact is the one the pre-change implementation produced
        # for this fixture: the merged Chinese of block 1 and the two rows that
        # were already empty stay exactly as they were delivered.
        assert read_srt_text(result.path) == EXPECTED_BILINGUAL_SRT
        # The original subtitle input is untouched as well.
        assert srt_path.read_text(encoding="utf-8") == before


def controlled_repair(requests: list[str], answer: str | None):
    """A controlled external call that records every request and then answers it.

    ``None`` stands for a request that fails outright.
    """

    def call(request: str) -> str:
        requests.append(request)
        if answer is None:
            raise RuntimeError("repair request failed")
        return answer

    return call


# Answers that are usable row by row and only fail to say which group they belong
# to: one that names no group at all, and one that also carries a group the
# request never asked about.
UNLABELLED_RESPONSE = "[1] 甲\n[2] 乙\n[3] 丙\n"
STRAY_GROUP_RESPONSE = "【组1】\n[1] 甲\n[2] 乙\n[3] 丙\n\n【组3】\n[1] 丁\n"


def test_a_response_that_cannot_be_attributed_keeps_the_request_scope():
    """C9.2/C10.1: an answer whose rows cannot be tied to the groups the request
    asked about is not used for any of them, and the chunk is still asked once."""
    with tempfile.TemporaryDirectory() as asked_tmp, tempfile.TemporaryDirectory() as plain_tmp:
        # Both sentence groups of this fixture carry a Chinese gap and travel in a
        # single request, so a row that names no group cannot be placed at all.
        srt_path = write_products(Path(asked_tmp), chinese_rows=MIXED_CHINESE_ROWS)
        english_rows, chinese_rows = read_products(srt_path)

        reference_srt = write_products(Path(plain_tmp), chinese_rows=MIXED_CHINESE_ROWS)
        reference = generate_bilingual_srt(reference_srt, chinese_rows, english_rows=english_rows)
        assert reference.ok, reference.error
        expected = read_srt_text(reference.path)

        for answer in (None, "", UNLABELLED_RESPONSE, STRAY_GROUP_RESPONSE):
            requests: list[str] = []
            result = generate_bilingual_srt(
                srt_path,
                chinese_rows,
                english_rows=english_rows,
                model_call=controlled_repair(requests, answer),
            )

            assert result.ok, result.error
            assert len(requests) == 1, (answer, requests)
            artifact = read_srt_text(result.path)
            assert artifact == expected, answer
            assert "甲" not in artifact, answer


# One request, two problem groups, and an answer whose first group rewrites an
# English word: that group keeps its own English, Chinese and timecodes, while the
# second group's usable answer is adopted.  One request does not mean one fate.
GROUP_ONE_INVALID_GROUP_TWO_VALID = (
    "【组1】\n"
    "英文：\n"
    "[1] This is one reason why it was so important for Capcom to never\n"
    "[2] reveal that Resident Evil 4 is\n"
    "[3] using a dynamic difficulty system.\n"
    "中文：\n"
    "[1] 这就是为什么卡普空绝口不提《生化危机4》\n"
    "[2] 使用了动态难度系统的原因之一。\n"
    "[3] （空）\n"
    "\n"
    "【组2】\n"
    "[1] 这真的很可惜，因为现在大部分游戏教程\n"
    "[2] 其实做得相当不错了。\n"
)

# Blocks 1-3 as the fixture already had them, blocks 4-5 carrying the adopted
# reallocation of the second group's answer.
MIXED_HALF_REPAIRED_BILINGUAL_SRT = (
    "1\n"
    "00:00:10,000 --> 00:00:12,500\n"
    "This is one reason why it was so important for Capcom to never\n"
    "这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。\n"
    "\n"
    "2\n"
    "00:00:12,500 --> 00:00:15,000\n"
    "reveal that Resident Evil 4 was\n"
    "\n"
    "\n"
    "3\n"
    "00:00:15,000 --> 00:00:18,200\n"
    "using a dynamic difficulty system.\n"
    "使用了动态难度系统。\n"
    "\n"
    "4\n"
    "00:00:18,200 --> 00:00:21,000\n"
    "And this is a shame, because for the most part video game\n"
    "这真的很可惜，因为现在大部分游戏教程\n"
    "\n"
    "5\n"
    "00:00:21,000 --> 00:00:23,400\n"
    "tutorials are actually pretty good these days.\n"
    "其实做得相当不错了。\n"
    "\n"
)


def test_one_invalid_group_does_not_discount_another_groups_valid_candidate():
    """C9.1: the answer is applied group by group — the group whose candidate is
    invalid keeps its own English, Chinese and timecodes, while a valid group of
    the same request is adopted."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp), chinese_rows=MIXED_CHINESE_ROWS)
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=controlled_repair(requests, GROUP_ONE_INVALID_GROUP_TWO_VALID),
        )

        assert result.ok, result.error
        assert len(requests) == 1, requests
        assert read_srt_text(result.path) == MIXED_HALF_REPAIRED_BILINGUAL_SRT


def test_an_answer_without_section_labels_is_used_when_only_one_group_was_asked():
    """C9.2: with a single group in the request there is nothing to attribute a
    label-less answer to but that group, so it is used as its Chinese."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=controlled_repair([], RESPLIT_RESPONSE.replace("【组1】\n", "")),
        )

        assert result.ok, result.error
        assert read_srt_text(result.path) == RESPLIT_EXPECTED_BILINGUAL_SRT


def test_a_real_failure_is_never_reported_as_a_rolled_back_repair():
    """C9.4: an input that does not add up and an artifact that cannot be written
    end the run as real failures; the repair around them is neither asked about the
    first nor blamed for the second."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        srt_path = write_products(root)
        english_rows, chinese_rows = read_products(srt_path)
        before = srt_path.read_text(encoding="utf-8")
        artifact = root / f"Bilingual_{srt_path.name}"

        # One Chinese row too few: the subtitle does not add up, so it is reported
        # as such and no repair is ever asked about it.
        requests: list[str] = []
        result = generate_bilingual_srt(
            srt_path,
            chinese_rows[:-1],
            english_rows=english_rows,
            model_call=controlled_repair(requests, RESPLIT_RESPONSE),
        )

        assert not result.ok
        assert result.path is None
        assert "4" in result.error and "5" in result.error, result.error
        assert requests == []
        assert not artifact.exists()

        # A usable repair and an artifact that cannot be written: the write is the
        # failure that is reported, even though the repair answered.
        (root / f"{artifact.name}.partial").mkdir()
        requests = []
        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=controlled_repair(requests, RESPLIT_RESPONSE),
        )

        assert not result.ok
        assert result.path is None
        assert "cannot write" in result.error, result.error
        assert len(requests) == 1, requests
        assert srt_path.read_text(encoding="utf-8") == before
        assert not artifact.exists()


# --- The external call the two real paths hand to the repair ----------------
#
# The normal path (the batch translate script) and the retry/continue path
# (worker._do_retry) both deliver their artifact through S1 and both hand it the
# same real external call.  That call asks once: an answer it cannot get is left
# to the repair's rollback instead of being tried again, so a repair never turns
# into a second round of model requests, let alone a concurrent one.


def test_the_repair_call_asks_once_and_hands_the_answer_back():
    """C10.2: the external call makes exactly one attempt — it neither retries a
    failure itself nor adds an attempt of its own."""
    request = "【组1】\n中文：\n[1] （空）\n"

    with patch("worker.skill_caller.call_skill", return_value="【组1】\n[1] 乙\n") as call:
        assert translate_module.repair_alignment_call(request) == "【组1】\n[1] 乙\n"

    assert call.call_count == 1, call.call_args_list
    assert call.call_args.kwargs["user_message"] == request

    raised: Exception | None = None
    with patch("worker.skill_caller.call_skill", side_effect=RuntimeError("api down")) as call:
        try:
            translate_module.repair_alignment_call(request)
        except RuntimeError as exc:
            raised = exc

    assert raised is not None
    assert call.call_count == 1, call.call_args_list


def test_the_repair_never_runs_beside_a_translation_request():
    """C10.3: the repair adds no model request of its own while a translation is in
    flight and never runs two of its own at once, so it cannot push the pipeline's
    model concurrency above the translation concurrency it already had."""
    translation_active = 0
    translation_peak = 0
    repair_active = 0
    repair_peak = 0
    overlap: list[int] = []

    def retranslate(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
        nonlocal translation_active, translation_peak
        translation_active += 1
        translation_peak = max(translation_peak, translation_active)
        try:
            write_flat_lines(Path(output_path), CHINESE_ROWS[2:])
            return True, ""
        finally:
            translation_active -= 1

    def repair(request: str) -> str:
        nonlocal repair_active, repair_peak
        repair_active += 1
        repair_peak = max(repair_peak, repair_active)
        try:
            overlap.append(translation_active)
            return no_answer(request)
        finally:
            repair_active -= 1

    with tempfile.TemporaryDirectory() as tmp:
        run = run_retry_path(Path(tmp), repair, retranslate)

    assert run.artifact == EXPECTED_BILINGUAL_SRT
    # The repair's one request ran with no translation request open, and no second
    # repair request ran beside it.
    assert overlap == [0], overlap
    assert repair_peak == 1
    # The retry path's own concurrency is unchanged: it redid its chunks serially.
    assert translation_peak == 1


def test_normal_translation_keeps_its_own_attempts():
    """C10.3: the repair adds nothing to how often the translation itself is
    attempted — a translation that cannot reach the model is still attempted its
    own three times, and the repair call is not involved in that at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chunk = root / "chunk_001.txt"
        write_flat_lines(chunk, ["Hello.", "There."])

        with patch("worker.skill_caller.call_skill", side_effect=RuntimeError("api down")) as call:
            ok, error = translate_module.translate_chunk(chunk, root / "chunk_001_chinese.txt")

    assert not ok and error
    assert call.call_count == 3, call.call_args_list


# The three-chunk fixture as it comes back when the request for its first chunk
# fails and the one for its third chunk is answered: the first group keeps its own
# rows, the third group carries the rows it was answered with, and the untouched
# second group stays where it was.
SPLIT_HALF_REPAIRED_BILINGUAL_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:03,000\n"
    "The first group opens here\n"
    "第一组从这里开始\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:05,000\n"
    "and it closes here.\n"
    "\n"
    "\n"
    "3\n"
    "00:00:05,000 --> 00:00:07,000\n"
    "The second group is already done\n"
    "第二组已经完成\n"
    "\n"
    "4\n"
    "00:00:07,000 --> 00:00:09,000\n"
    "and needs no repair.\n"
    "不需要修复\n"
    "\n"
    "5\n"
    "00:00:09,000 --> 00:00:11,000\n"
    "The third group is not done yet\n"
    "第三组从这里开始\n"
    "\n"
    "6\n"
    "00:00:11,000 --> 00:00:13,000\n"
    "and still has a gap.\n"
    "还有空缺。\n"
    "\n"
)


def test_a_failed_request_leaves_only_its_own_request_scope_alone():
    """C9.2: a request that fails keeps the scope it was asked about and nothing
    else — another Translation Chunk is still repaired from its own answer."""
    with tempfile.TemporaryDirectory() as tmp:
        srt_path = write_split_products(Path(tmp))
        english_rows, chinese_rows = read_products(srt_path)
        requests: list[str] = []
        answer = "【组1】\n[1] 第三组从这里开始\n[2] 还有空缺。\n"

        def call(request: str) -> str:
            requests.append(request)
            if len(requests) == 1:
                raise RuntimeError("repair request failed")
            return answer

        result = generate_bilingual_srt(
            srt_path,
            chinese_rows,
            english_rows=english_rows,
            model_call=call,
        )

        assert result.ok, result.error
        assert len(requests) == 2, requests
        assert read_srt_text(result.path) == SPLIT_HALF_REPAIRED_BILINGUAL_SRT


# --- The finalized artifact the video directory holds ------------------------
#
# What these promises are about is the subtitle a run delivers, so the runs below
# go the whole way and read that file back: a real call site assembles the
# Bilingual SRT through the module, the real ``worker._do_finalize`` runs
# ``finalize-subtitles.ps1`` and moves the subtitle into the video directory, and
# every assertion reads the file the video directory holds — never an intermediate
# ``_chinese.txt`` and never an object on the way there.


@dataclass(frozen=True)
class JobFixture:
    """A Job's subtitle and the Translation Chunk products that translated it."""

    name: str
    srt_text: str
    english_rows: list[str]
    chinese_rows: list[str]
    chunk_sizes: tuple[int, ...]

    def rows_of_chunk(self, chunk_name: str) -> list[str]:
        """The Chinese rows the chunk file of that name carries."""
        position = int(Path(chunk_name).stem.split("_")[1])
        start = sum(self.chunk_sizes[: position - 1])
        return self.chinese_rows[start : start + self.chunk_sizes[position - 1]]


MOVE_SPLIT_JOB = JobFixture(
    "Move The Split.en.srt",
    SPLIT_MOVE_SRT_TEXT,
    SPLIT_MOVE_ENGLISH_ROWS,
    SPLIT_MOVE_CHINESE_ROWS,
    (4,),
)

# The same subtitle as the translation stage chunked it in two: the sentence group
# that may be repaired lies in the first chunk, and the chunk holding the sentence
# after it is the one a run that failed there has to redo.
MOVE_SPLIT_TWO_CHUNK_JOB = JobFixture(
    "Move The Split.en.srt",
    SPLIT_MOVE_SRT_TEXT,
    SPLIT_MOVE_ENGLISH_ROWS,
    SPLIT_MOVE_CHINESE_ROWS,
    (3, 1),
)

# Two sentence groups in two Translation Chunks: the first one may have its split
# moved and is answered, the second one is answered in a way that cannot be used
# and has to keep its own version — the two halves of one delivered subtitle.
MIXED_REPAIR_SRT_TEXT = """1
00:00:02,000 --> 00:00:04,000
move this word

2
00:00:04,000 --> 00:00:07,000
into the next block

3
00:00:07,000 --> 00:00:11,000
if it fits there.

4
00:00:13,000 --> 00:00:14,000
leave this here

5
00:00:14,000 --> 00:00:15,000
and that there.
"""

MIXED_REPAIR_ENGLISH_ROWS = [
    "move this word",
    "into the next block",
    "if it fits there.",
    "leave this here",
    "and that there.",
]

MIXED_REPAIR_CHINESE_ROWS = ["把这个词", "", "如果放得下", "把这里留下", ""]

MIXED_REPAIR_JOB = JobFixture(
    "Mixed Repair.en.srt",
    MIXED_REPAIR_SRT_TEXT,
    MIXED_REPAIR_ENGLISH_ROWS,
    MIXED_REPAIR_CHINESE_ROWS,
    (3, 2),
)

# A subtitle whose first sentence group already carries the times an accepted
# repair gave it: "move this" runs 2,000-2,900 ms instead of the 2,000-4,000 ms of
# the group's own block, and its neighbour starts at 3,100 ms.  Its second block
# still has no Chinese, so a run that continues over this subtitle repairs the same
# group again — and whatever split it moves there, it has to estimate from these
# timecodes and no others.
ADJUSTED_JOB = JobFixture(
    "Adjusted Times.en.srt",
    ADJUSTED_SRT_TEXT,
    ADJUSTED_ENGLISH_ROWS,
    ADJUSTED_CHINESE_ROWS,
    (2, 1),
)

# The answer moves the split one word down.  The times of the delivered file follow
# from the timecodes this subtitle already carries: block 1 ends where "this" ends
# (2,400 ms — 2,000 plus 4/9 of the 900 ms the block was given — and not the
# 2,888 ms an estimate from the 2,000-4,000 ms the block had before it was adjusted
# would give), while block 2 starts where "this" starts and still ends on the last
# word of the block that held it, pinned to 7,000 ms.
ADJUSTED_MOVE_ANSWER = build_repair_answer(
    ["move", "this word into the next block", "if it fits there."],
    ["移", "这个词到下一块", "如果放得下"],
)


@dataclass(frozen=True)
class FinalBlock:
    """One block of the finalized Bilingual SRT, as the video directory holds it."""

    start: str
    end: str
    english: str
    chinese: str


def read_final_blocks(path: Path) -> list[FinalBlock]:
    """Read a finalized Bilingual SRT, block by block.

    Every block holds its English row and then its Chinese row, which is blank
    where the translation left a gap, and one blank line separates it from the next.
    """
    lines = read_srt_text(path).split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the newline that ends the file
    starts = [
        index
        for index in range(len(lines) - 1)
        if lines[index].isdigit() and " --> " in lines[index + 1]
    ]
    blocks: list[FinalBlock] = []
    for position, begin in enumerate(starts):
        stop = starts[position + 1] if position + 1 < len(starts) else len(lines)
        content = lines[begin + 2 : stop]
        if content and content[-1] == "":
            content.pop()  # the separator
        start, _, end = lines[begin + 1].partition(" --> ")
        blocks.append(
            FinalBlock(
                start,
                end,
                "\n".join(content[:-1]),
                content[-1] if content else "",
            )
        )
    return blocks


@dataclass(frozen=True)
class FinalizedRun:
    """What a real call site left in the video directory.

    ``artifact`` is the finalized Bilingual SRT read back from the video
    directory, ``repair_requests`` every request the run handed the repair's
    external call, ``translated_chunks`` the Translation Chunks the run translated
    itself, and ``s1_artifacts`` the text the Bilingual SRT module wrote — so a
    test can say which step delivered the finalized file.
    """

    artifact: str
    path: Path
    video_dir: Path
    repair_requests: list[str]
    translated_chunks: list[str]
    s1_artifacts: list[str]
    finished: bool


def _final_blocks(run: FinalizedRun) -> list[tuple[str, str, str, str]]:
    """The delivered English, Chinese and times of every block, block by block."""
    return [
        (block.english, block.chinese, block.start, block.end)
        for block in read_final_blocks(run.path)
    ]


@contextmanager
def worker_home(tmp_root: Path):
    """Point the worker at a temporary home with its own video directory."""
    video_dir = tmp_root / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    config = tmp_root / "config.json"
    config.write_text(
        json.dumps({"db_path": str(tmp_root / "jobs.db"), "video_dir": str(video_dir)}),
        encoding="utf-8",
    )
    previous = (worker._conn, worker._config, worker._CONFIG_PATH, worker._ROOT)
    worker._conn = None
    worker._config = {}
    worker._CONFIG_PATH = config
    worker._ROOT = tmp_root
    try:
        yield video_dir
    finally:
        if worker._conn is not None:
            worker._conn.close()
        worker._conn, worker._config, worker._CONFIG_PATH, worker._ROOT = previous


def _start_job(tmp_root: Path, fixture: JobFixture, failed_chunks: tuple[int, ...]):
    """Write a Job's real subtitle and chunk products and give it a database row."""
    job_id = worker._create_job("https://youtube.com/watch?v=finalized001", "finalized001")
    job_dir = tmp_root / "jobs" / job_id
    job_dir.mkdir(parents=True)
    srt_path = write_chunked_products(
        job_dir,
        fixture.name,
        fixture.srt_text,
        fixture.english_rows,
        fixture.chinese_rows,
        fixture.chunk_sizes,
    )
    chunks = job_dir / f"{srt_path.stem}_workspace" / "chunks"
    for index in failed_chunks:
        (chunks / f"chunk_{index:03d}_chinese.txt").unlink()
    worker._update_job(
        job_id,
        status="failed",
        stage="translating",
        progress=f"{len(fixture.chunk_sizes) - len(failed_chunks)}/{len(fixture.chunk_sizes)}",
        error="翻译失败",
    )
    return job_id, srt_path


def _record_s1_delivery(module, s1_artifacts: list[str]):
    """Wrap a call site's use of the module so a test sees what it wrote."""
    real = generate_bilingual_srt

    def delivered(*args, **kwargs):
        result = real(*args, **kwargs)
        s1_artifacts.append(read_srt_text(result.path) if result.ok and result.path else "")
        return result

    return patch.object(module, "generate_bilingual_srt", side_effect=delivered)


def run_normal_path_to_video(
    tmp_root: Path, model_call, fixture: JobFixture
) -> FinalizedRun:
    """Drive the normal translation path and the real finalize step.

    Runs the batch translate script — the normal path's own assembly — over the
    products in the Job directory, then lets ``worker._do_finalize`` run
    ``finalize-subtitles.ps1`` and move the subtitle into the video directory.
    """
    with worker_home(tmp_root) as video_dir:
        job_id, srt_path = _start_job(tmp_root, fixture, ())
        requests, s1_artifacts = _assemble_with_batch_script(srt_path, model_call)
        finished = worker._do_finalize(job_id, srt_path)

    path = video_dir / srt_path.name
    return FinalizedRun(
        read_srt_text(path), path, video_dir, requests, [], s1_artifacts, finished
    )


def run_retry_path_to_video(
    tmp_root: Path, model_call, fixture: JobFixture, failed_chunks: tuple[int, ...]
) -> FinalizedRun:
    """Drive the retry/continue path and the real finalize step.

    Only the chunks named in ``failed_chunks`` are translated again; every other
    chunk keeps the rows it already delivered.
    """
    with worker_home(tmp_root) as video_dir:
        job_id, srt_path = _start_job(tmp_root, fixture, failed_chunks)
        requests: list[str] = []
        translated: list[str] = []
        s1_artifacts: list[str] = []

        def call(request: str) -> str:
            requests.append(request)
            return model_call(request)

        def retranslate(chunk_path: Path, output_path: Path) -> tuple[bool, str]:
            translated.append(Path(chunk_path).name)
            write_flat_lines(Path(output_path), fixture.rows_of_chunk(Path(chunk_path).name))
            return True, ""

        with patch.object(translate_module, "translate_chunk", side_effect=retranslate), patch.object(
            translate_module, "repair_alignment_call", side_effect=call
        ), _record_s1_delivery(bilingual_srt_module, s1_artifacts):
            worker._do_retry(job_id)

        job = worker._get_job(job_id) or {}
        finished = job.get("stage") == "done" and job.get("status") == "success"

    path = video_dir / srt_path.name
    return FinalizedRun(
        read_srt_text(path), path, video_dir, requests, translated, s1_artifacts, finished
    )


def answer_in_turn(*answers: str):
    """A controlled external call that answers each request with the next answer."""
    remaining = list(answers)

    def call(request: str) -> str:
        return remaining.pop(0) if remaining else ""

    return call


def test_the_finalized_artifact_carries_the_adopted_split_with_its_chinese_and_times():
    """C11.1: the subtitle the video directory holds after the finalize step gives
    the adopted group the new English split, the new Chinese rows and the times the
    program estimated for that split — all three together, block by block."""
    with tempfile.TemporaryDirectory() as tmp:
        run = run_normal_path_to_video(
            Path(tmp), lambda request: MOVED_SPLIT_RESPONSE, MOVE_SPLIT_JOB
        )

        assert run.finished
        assert run.path.parent == run.video_dir
        # The video directory holds the subtitle under its own name: the artifact
        # the finalize step renamed, and nothing else.
        assert run.path.name == MOVE_SPLIT_JOB.name
        assert [entry.name for entry in run.video_dir.iterdir()] == [MOVE_SPLIT_JOB.name]
        # What the run reads back is the finalized file alone: the Job directory
        # with its aggregated Chinese text is gone, so nothing intermediate stands
        # in for it.
        assert list(run.video_dir.parent.glob("jobs/*")) == []
        assert _final_blocks(run) == [
            ("move this", "把这个", "00:00:02,000", "00:00:03,285"),
            ("word into the next block", "词移到下一块", "00:00:03,428", "00:00:07,000"),
            ("if it fits there.", "如果放得下", "00:00:07,000", "00:00:11,000"),
            ("done.", "完成。", "00:00:13,000", "00:00:14,000"),
        ]
        # No block carries the new English with the timecode the split had before
        # the move.
        assert "00:00:02,000 --> 00:00:04,000" not in run.artifact
        assert "00:00:04,000 --> 00:00:07,000" not in run.artifact


def test_the_finalized_artifact_keeps_every_group_at_one_version():
    """C11.2: in one delivered subtitle the group whose answer was adopted carries
    its new English, new Chinese and new times, while the group whose answer could
    not be used keeps its own three — group by group, with nothing mixed."""
    with tempfile.TemporaryDirectory() as tmp:
        run = run_retry_path_to_video(
            Path(tmp),
            answer_in_turn(MOVED_SPLIT_RESPONSE, INCOMPLETE_RESPONSE),
            MIXED_REPAIR_JOB,
            (2,),
        )

        assert run.finished
        assert len(run.repair_requests) == 2, run.repair_requests
        assert _final_blocks(run) == [
            # The adopted group: the moved split, its own Chinese and the times the
            # program estimated for that split.
            ("move this", "把这个", "00:00:02,000", "00:00:03,285"),
            ("word into the next block", "词移到下一块", "00:00:03,428", "00:00:07,000"),
            ("if it fits there.", "如果放得下", "00:00:07,000", "00:00:11,000"),
            # The rolled-back group: its own English, its own Chinese and its own
            # times, untouched by the answer that could not be used.
            ("leave this here", "把这里留下", "00:00:13,000", "00:00:14,000"),
            ("and that there.", "", "00:00:14,000", "00:00:15,000"),
        ]
        # Nothing from the unusable answer reached a block of the rolled-back
        # group, and neither group took the other's version.
        assert "卡普空" not in run.artifact


def test_the_normal_path_and_the_continuing_path_deliver_the_same_finalized_subtitle():
    """C12.1: the same subtitle and Translation Chunk products with the same
    controlled candidate, taken once through the normal path and once through the
    path that fails a chunk first and continues, leave the same subtitle in the
    video directory — content and times alike."""
    with tempfile.TemporaryDirectory() as normal_tmp, tempfile.TemporaryDirectory() as retry_tmp:
        normal = run_normal_path_to_video(
            Path(normal_tmp), lambda request: MOVED_SPLIT_RESPONSE, MOVE_SPLIT_JOB
        )
        retry = run_retry_path_to_video(
            Path(retry_tmp), lambda request: MOVED_SPLIT_RESPONSE, MOVE_SPLIT_JOB, (1,)
        )

        assert normal.finished and retry.finished
        assert normal.artifact == MOVED_SPLIT_EXPECTED_BILINGUAL_SRT
        assert retry.artifact == normal.artifact
        assert _final_blocks(retry) == _final_blocks(normal)
        # The continuing path really continued: it redid the chunk that had failed
        # and asked the repair for the same chunk the normal path asked about.
        assert retry.translated_chunks == ["chunk_001.txt"]
        assert len(retry.repair_requests) == len(normal.repair_requests) == 1


def test_continuing_over_a_failed_chunk_keeps_the_chunk_that_succeeded():
    """C12.2: the continuing run translates only the Translation Chunk that failed,
    so the chunk that had already delivered its rows keeps them, and the finalized
    subtitle is the artifact the module wrote rather than something assembled
    around it."""
    with tempfile.TemporaryDirectory() as tmp:
        run = run_retry_path_to_video(
            Path(tmp), lambda request: MOVED_SPLIT_RESPONSE, MOVE_SPLIT_TWO_CHUNK_JOB, (2,)
        )

        assert run.finished
        # The subtitle was not translated again: only the chunk that had failed ran.
        assert run.translated_chunks == ["chunk_002.txt"]
        # The chunk that had succeeded still carries its own rows — the repair's
        # reallocation of them included — into the delivered subtitle.
        assert run.artifact == MOVED_SPLIT_EXPECTED_BILINGUAL_SRT
        # The video directory holds exactly what the module wrote, renamed by the
        # finalize step: the accepted results were not lost on the way there.
        assert run.s1_artifacts == [run.artifact]
        assert run.path.name == MOVE_SPLIT_TWO_CHUNK_JOB.name


def test_continuing_over_an_already_adjusted_subtitle_does_not_shift_its_times():
    """C12.2: a continuing run over a subtitle that already carries the times an
    accepted repair gave its group estimates the split it moves from those very
    timecodes, so repeated processing cannot drift the subtitle's times."""
    with tempfile.TemporaryDirectory() as tmp:
        run = run_retry_path_to_video(
            Path(tmp), lambda request: ADJUSTED_MOVE_ANSWER, ADJUSTED_JOB, (2,)
        )

        assert run.finished
        # The continuing run really continued: only the chunk that had failed ran.
        assert run.translated_chunks == ["chunk_002.txt"]
        assert run.artifact == ADJUSTED_EXPECTED_BILINGUAL_SRT
        assert _final_blocks(run) == [
            ("move", "移", "00:00:02,000", "00:00:02,400"),
            ("this word into the next block", "这个词到下一块", "00:00:02,500", "00:00:07,000"),
            ("if it fits there.", "如果放得下", "00:00:07,000", "00:00:11,000"),
        ]


if __name__ == "__main__":
    tests = [
        test_generates_bilingual_srt_matching_pre_change_output,
        test_validate_block_count_requires_one_row_per_block,
        test_rejects_chinese_rows_that_do_not_match_blocks,
        test_rebuilds_sentence_groups_with_the_translation_stage_rule,
        test_sentence_group_rule_handles_closing_quotes_and_a_trailing_group,
        test_validate_group_ranges_requires_groups_that_cover_each_block_once,
        test_validate_time_legality_requires_forward_ordered_blocks,
        test_rejects_a_subtitle_whose_timecodes_do_not_run_forward,
        test_reports_failure_for_a_subtitle_it_cannot_read,
        test_english_content_preservation_is_not_fooled_by_whitespace,
        test_rejects_english_product_that_disagrees_with_the_subtitle,
        test_repair_request_carries_the_triggered_group_and_its_chinese,
        test_repair_is_one_request_per_translation_chunk,
        test_one_request_carries_every_triggered_group_of_a_chunk,
        test_a_group_cut_by_a_chunk_boundary_is_asked_once_with_all_its_blocks,
        test_accepted_chinese_candidate_is_placed_per_block,
        test_unusable_candidate_keeps_the_whole_group,
        test_no_repair_request_when_every_group_has_chinese,
        test_normal_and_retry_paths_deliver_the_same_bilingual_srt,
        test_both_paths_ask_the_repair_once_and_apply_its_answer,
        test_a_failed_repair_still_finishes_both_paths_with_the_translated_subtitle,
        test_moved_english_split_lands_its_new_english_and_estimated_times,
        test_timecodes_a_candidate_writes_never_reach_the_artifact,
        test_an_english_answer_that_keeps_the_original_split_keeps_every_timecode,
        test_unusable_english_moves_leave_the_whole_group_alone,
        test_a_moved_split_without_a_usable_chinese_part_is_not_half_applied,
        test_a_moved_split_that_cannot_be_given_legal_times_leaves_the_group_alone,
        test_the_estimate_is_based_on_the_timecodes_the_call_received,
        test_a_moved_split_may_not_re_cut_the_subtitles_sentence_groups,
        test_a_repair_request_that_fails_keeps_the_original_result,
        test_a_response_that_cannot_be_attributed_keeps_the_request_scope,
        test_one_invalid_group_does_not_discount_another_groups_valid_candidate,
        test_an_answer_without_section_labels_is_used_when_only_one_group_was_asked,
        test_a_real_failure_is_never_reported_as_a_rolled_back_repair,
        test_a_failed_request_leaves_only_its_own_request_scope_alone,
        test_the_repair_call_asks_once_and_hands_the_answer_back,
        test_the_repair_never_runs_beside_a_translation_request,
        test_normal_translation_keeps_its_own_attempts,
        test_the_finalized_artifact_carries_the_adopted_split_with_its_chinese_and_times,
        test_the_finalized_artifact_keeps_every_group_at_one_version,
        test_the_normal_path_and_the_continuing_path_deliver_the_same_finalized_subtitle,
        test_continuing_over_a_failed_chunk_keeps_the_chunk_that_succeeded,
        test_continuing_over_an_already_adjusted_subtitle_does_not_shift_its_times,
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
