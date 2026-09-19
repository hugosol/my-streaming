"""Bilingual SRT generation — seam S1.

Turns a pre-repair timed English subtitle plus the ordered, whitespace-preserving
Chinese rows delivered by the Translation Chunks into the actual Bilingual SRT
artifact.  The normal translation path (``worker/scripts/batch_translate.py``)
and the retry path (``worker._do_retry``) both deliver their artifact here, so a
subtitle block's English text, Chinese row and timecodes always come from one
accepted result instead of being reassembled by each caller.

The deterministic checks the module runs before it writes (block count, English
content preservation, sentence-group range, time legality) are exposed as module
functions, so a local alignment repair can validate its candidates with the very
same rules.

The optional local alignment repair closes the Chinese rows a sentence group
still holds empty: the triggered groups of one Translation Chunk travel in a
single request through the injected external call, and a group whose answer is
missing or unusable keeps its own English, Chinese and timecodes.  An answer may
reallocate and rewrite the group's Chinese, and it may move where the group's
English is cut — never a word, its order or its punctuation.  A moved split is
given timecodes the module estimates itself from the group's pre-repair
character positions and timecodes, following the punctuation stage's way of
estimating times (``worker/scripts/resegment.py``); the candidate's own
timecodes are never used.

The repair is optional and asks once: it is never retried, and neither a request
that fails nor an answer whose rows cannot be attributed to the groups the request
carried is guessed at — that request scope keeps the result it already had, whole
group by whole group, so a delivered block never mixes a new version with an old
one.  A subtitle that does not add up or an artifact that cannot be written is a
failure of its own and is never passed off as a repair that rolled itself back.

Anything that does not add up — an unusable SRT, rows that do not match the
subtitle blocks, English that lost or changed words, illegal times, a failed
write — leaves the original subtitle input untouched, produces no partial
Bilingual SRT and is reported upward as a failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from worker.translate import read_flat_lines, split_english_groups

# Injected external model call: request text in, raw model output out.  The
# local alignment repair is its only consumer.
ModelCall = Callable[[str], str]

_TIME_FIELD = r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
_TIMECODE_RE = re.compile(rf"{_TIME_FIELD}\s*-->\s*{_TIME_FIELD}")
_BLOCK_NUMBER_RE = re.compile(r"\d+")
_LINE_SPLIT_RE = re.compile(r"\r\n|\n|\r")
_GROUP_LABEL_RE = re.compile(r"^【\s*组\s*(\d+)\s*】")
_POSITION_RE = re.compile(r"^\[(\d+)\]\s*(.*)$")
_SEPARATOR_RE = re.compile(r"^[\-=*_]{3,}$")
_WORD_RE = re.compile(r"\S+")

# A row without Chinese is written as this marker, both in the request (so the
# model sees which positions are still empty) and in its answer (so it can say
# that a position stays empty).
_CHINESE_GAP = "（空）"
_GAP_TOKENS = frozenset({_CHINESE_GAP, "(空)", "（空白）", "(空白)"})

# The two parts a group's answer may carry.  The request lists a group's English
# blocks first and its Chinese rows after that, so these labels also serve as the
# section markers of an answer that gives both.
_ENGLISH_LANGUAGE = "英文"
_CHINESE_LANGUAGE = "中文"
_ENGLISH_LABEL = f"{_ENGLISH_LANGUAGE}："
_CHINESE_LABEL = f"{_CHINESE_LANGUAGE}："
_LANGUAGE_LABEL_RE = re.compile(rf"^({_ENGLISH_LANGUAGE}|{_CHINESE_LANGUAGE})\s*[:：]?\s*$")


class SrtInputError(ValueError):
    """The subtitle input cannot be read as usable timed English subtitles."""


@dataclass(frozen=True)
class SubtitleBlock:
    """One numbered SRT entry and the Chinese row that carries its translation."""

    number: int
    start_ms: int
    end_ms: int
    text_lines: tuple[str, ...]
    chinese: str = ""

    @property
    def english(self) -> str:
        """The block's English as one flat row, as the Translation Chunks carry it."""
        return " ".join(self.text_lines)


@dataclass(frozen=True)
class SentenceGroup:
    """A run of consecutive subtitle blocks the translator handled as one unit."""

    index: int
    block_indices: tuple[int, ...]


@dataclass(frozen=True)
class BilingualSrtResult:
    """The delivered artifact, or the failure that kept the original input."""

    ok: bool
    path: Path | None
    error: str


def _to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + int(millis)


def format_timecode(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_timed_english_srt(text: str) -> tuple[SubtitleBlock, ...]:
    """Read the timed English subtitle into blocks.

    Raises :class:`SrtInputError` for anything that cannot be assembled later:
    a block without a number, without a usable timecode line, or without English
    text.  Block order and numbering are kept exactly as they appear.
    """
    blocks: list[SubtitleBlock] = []
    pending: list[str] = []

    for raw in _LINE_SPLIT_RE.split(text):
        line = raw.strip()
        if not line:
            if pending:
                blocks.append(_build_block(pending))
                pending = []
            continue
        pending.append(line)
    if pending:
        blocks.append(_build_block(pending))

    if not blocks:
        raise SrtInputError("no subtitle blocks found")
    return tuple(blocks)


def _build_block(lines: list[str]) -> SubtitleBlock:
    heading = lines[0]
    if not _BLOCK_NUMBER_RE.fullmatch(heading):
        raise SrtInputError(f"subtitle block does not start with a number: {heading!r}")
    if len(lines) < 2:
        raise SrtInputError(f"subtitle block {heading} has no timecode line")
    match = _TIMECODE_RE.fullmatch(lines[1])
    if match is None:
        raise SrtInputError(
            f"subtitle block {heading} has an unusable timecode line: {lines[1]!r}"
        )
    text_lines = tuple(lines[2:])
    if not text_lines:
        raise SrtInputError(f"subtitle block {heading} has no English text")
    return SubtitleBlock(
        number=int(heading),
        start_ms=_to_ms(*match.groups()[:4]),
        end_ms=_to_ms(*match.groups()[4:]),
        text_lines=text_lines,
    )


def render_bilingual_srt(blocks: Sequence[SubtitleBlock]) -> str:
    """Render the artifact: each block, its Chinese row, then a blank separator."""
    rows: list[str] = []
    for block in blocks:
        rows.append(str(block.number))
        rows.append(f"{format_timecode(block.start_ms)} --> {format_timecode(block.end_ms)}")
        rows.extend(block.text_lines)
        rows.append(block.chinese)
        rows.append("")
    return "\n".join(rows) + "\n"


def _clean_row(row: str) -> str:
    """Drop line terminators a flat product written with CRLF leaves behind."""
    return row.strip("\r\n")


def _words(rows: Sequence[str]) -> list[str]:
    """The words of a row list, with block-boundary whitespace normalized away."""
    return " ".join(rows).split()


def rebuild_sentence_groups(blocks: Sequence[SubtitleBlock]) -> tuple[SentenceGroup, ...]:
    """Rebuild the translation stage's sentence groups from the subtitle blocks.

    Applies the translation stage's own rule (``split_english_groups``) to each
    block's flat English row, so the grouping used here cannot drift from the
    grouping the translator used.  Blocks keep their order and numbering: a group
    is a run of consecutive blocks, numbered the way the translator numbered them.
    """
    sizes = [len(group) for group in split_english_groups([block.english for block in blocks])]
    groups: list[SentenceGroup] = []
    start = 0
    for index, size in enumerate(sizes, 1):
        groups.append(SentenceGroup(index=index, block_indices=tuple(range(start, start + size))))
        start += size
    return tuple(groups)


def validate_block_count(
    blocks: Sequence[SubtitleBlock],
    chinese_lines: Sequence[str],
    english_rows: Sequence[str] | None = None,
) -> str | None:
    """Every row list must carry exactly one entry per subtitle block."""
    if len(chinese_lines) != len(blocks):
        return f"{len(chinese_lines)} Chinese rows for {len(blocks)} subtitle blocks"
    if english_rows is not None and len(english_rows) != len(blocks):
        return f"{len(english_rows)} English rows for {len(blocks)} subtitle blocks"
    return None


def validate_group_ranges(groups: Sequence[SentenceGroup], block_count: int) -> str | None:
    """Sentence groups must cover every block once, in order, without crossing."""
    expected = 0
    for group in groups:
        if not group.block_indices:
            return f"sentence group {group.index} has no subtitle blocks"
        if group.block_indices[0] != expected:
            return (
                f"sentence group {group.index} starts at subtitle block "
                f"{group.block_indices[0] + 1} instead of {expected + 1}"
            )
        for previous, current in zip(group.block_indices, group.block_indices[1:]):
            if current != previous + 1:
                return f"sentence group {group.index} skips subtitle block {previous + 2}"
        expected = group.block_indices[-1] + 1
    if expected != block_count:
        return f"sentence groups cover {expected} of {block_count} subtitle blocks"
    return None


def validate_english_preserved(
    english_rows: Sequence[str], candidate_rows: Sequence[str]
) -> str | None:
    """The candidate English must keep every word, in order, with its punctuation.

    Rows are compared as whitespace-separated tokens.  That is exactly the
    allowance the alignment repair needs to move a block boundary — which only
    changes the whitespace between words — while a rewritten, dropped, duplicated
    or reordered word, or changed punctuation, still fails.
    """
    source_words = _words(english_rows)
    candidate_words = _words(candidate_rows)
    if source_words == candidate_words:
        return None
    for position, (source_word, candidate_word) in enumerate(
        zip(source_words, candidate_words), 1
    ):
        if source_word != candidate_word:
            return f"English word {position} changed: {source_word!r} -> {candidate_word!r}"
    return f"English word count changed: {len(source_words)} -> {len(candidate_words)}"


def validate_time_legality(blocks: Sequence[SubtitleBlock]) -> str | None:
    """Every block must run forward in time and follow the block before it.

    Overlap with a neighbouring block is deliberately not rejected: the input's
    own overlap is not this module's business, while an adjusted candidate must
    not add new ones.
    """
    previous: SubtitleBlock | None = None
    for block in blocks:
        if block.end_ms <= block.start_ms:
            return f"subtitle block {block.number} has no positive duration"
        if previous is not None and block.start_ms < previous.start_ms:
            return (
                f"subtitle block {block.number} starts before subtitle block {previous.number}"
            )
        previous = block
    return None


def _failure(srt_path: Path, error: str) -> BilingualSrtResult:
    return BilingualSrtResult(False, None, f"{srt_path.name}: {error}")


def _is_chinese_gap(row: str) -> bool:
    """An answer row that says this position stays without Chinese."""
    return row.strip() in _GAP_TOKENS


def _group_with_a_chinese_gap(blocks: Sequence[SubtitleBlock], group: SentenceGroup) -> bool:
    """A group triggers the repair only through a block with English but no Chinese."""
    return any(
        blocks[index].english.strip() and not blocks[index].chinese.strip()
        for index in group.block_indices
    )


def _translation_chunk_spans(
    srt_path: Path, block_count: int
) -> tuple[tuple[int, int], ...] | None:
    """The Translation Chunk partition of the subtitle blocks, as half-open spans.

    A Translation Chunk is one ``chunk_NNN.txt`` / ``chunk_NNN_chinese.txt`` pair
    in the Workspace, and the aggregated Chinese rows are exactly those files'
    rows in the same order, so the chunk files are the only record of how many
    rows one repair request may carry.  ``None`` means that record is missing or
    does not add up to the subtitle at hand: without it a request cannot be
    scoped to a Translation Chunk, so the caller skips the repair.
    """
    chunks_dir = srt_path.parent / f"{srt_path.stem}_workspace" / "chunks"
    if not chunks_dir.is_dir():
        return None
    spans: list[tuple[int, int]] = []
    start = 0
    for chunk_file in sorted(chunks_dir.glob("chunk_*.txt")):
        if chunk_file.stem.endswith("_chinese"):
            continue
        try:
            rows = read_flat_lines(chunk_file.read_text(encoding="utf-8"))
        except OSError:
            return None
        if not rows:
            return None
        spans.append((start, start + len(rows)))
        start += len(rows)
    if not spans or start != block_count:
        return None
    return tuple(spans)


def _repair_request(blocks: Sequence[SubtitleBlock], groups: Sequence[SentenceGroup]) -> str:
    """One repair request for the triggered sentence groups of one chunk.

    Carries every English block of each group and that group's whole existing
    Chinese — the non-empty rows that carry a merged translation as well as the
    empty ones — so the model reallocates what is already translated instead of
    translating the same meaning again.  Nothing else leaves the module: no
    untriggered group, no other Translation Chunk, no whole-subtitle text.  Each
    group is shown with its two parts, the English blocks and the Chinese rows;
    the Chinese rows are the only part an answer has to speak about.
    """
    sections = [
        "以下字幕句组的英文已经有过中文翻译，但句组内仍有中文空缺。",
        "请先用该组已有的中文重新分配或改写：不要为填满空缺增添没有依据的意思，"
        "也不要改动英文的单词、顺序和标点。",
        "只有当原来的英文切分把一个短语割裂到两块时，才在同一句组内移动切分点："
        "单词、顺序和标点保持原样，行数不增不减，每行都要有英文。",
        f"每组按行号给出该组每一行的中文；仍然没有中文的位置写成 {_CHINESE_GAP}，"
        "行号不增不减。",
        f"移动了英文切分点的组，再用{_ENGLISH_LABEL}给出该组每一行的新英文；"
        "英文的时间码由程序计算，不要输出。",
    ]
    for ordinal, group in enumerate(groups, 1):
        english = "\n".join(
            f"[{position}] {blocks[index].english}"
            for position, index in enumerate(group.block_indices, 1)
        )
        chinese = "\n".join(
            f"[{position}] {blocks[index].chinese.strip() or _CHINESE_GAP}"
            for position, index in enumerate(group.block_indices, 1)
        )
        sections.append(
            f"【组{ordinal}】\n{_ENGLISH_LABEL}\n{english}\n{_CHINESE_LABEL}\n{chinese}"
        )
    return "\n\n".join(sections) + "\n"


@dataclass(frozen=True)
class _GroupAnswer:
    """One group's answer: the rows that re-partition its English, and its Chinese."""

    english: tuple[tuple[int, str], ...] = ()
    chinese: tuple[tuple[int, str], ...] = ()


_NO_ANSWER = _GroupAnswer()


def _answer_parts(
    sections: dict[int, dict[str, list[tuple[int, str]]]], ordinal: int
) -> dict[str, list[tuple[int, str]]]:
    """The two parts of one group's answer, created on first mention."""
    return sections.setdefault(ordinal, {_ENGLISH_LANGUAGE: [], _CHINESE_LANGUAGE: []})


def _read_repair_rows(response: str, group_count: int) -> dict[int, _GroupAnswer]:
    """Read one repair answer into the rows of each ``【组N】`` section.

    The section ordinal is the group's position in the request, which is the only
    thing that maps an answer back to a sentence group; a label may carry
    decoration after it.  A section may carry two parts: the English rows that
    re-partition the group, and the Chinese rows.  Either part is introduced by its
    own ``英文：`` / ``中文：`` label, and — because the request lists a group's
    English blocks before its Chinese — rows written ahead of a section's first
    language label are its English rows.  Anything the answer says outside that
    shape — a preamble, a separator, a stray English row — is not a row and is
    ignored.

    An answer is only read when its rows can be attributed at all: every section
    has to name a group the request carried, no group may be named twice, and every
    row has to stand inside a section.  An answer that does not add up — a section
    for a group nobody asked about, a group answered twice, or rows that name no
    group while several were asked about — has its correspondence guessed at
    nowhere: the whole request scope it answers keeps the rows it already had.  A
    request that carried a single group is the one answer that cannot be
    misattributed: there, rows without any section label can only be that group's,
    so they are read as its Chinese.
    """
    sections: dict[int, dict[str, list[tuple[int, str]]]] = {}
    unlabelled: dict[int, list[tuple[int, str]]] = {}
    named: list[int] = []
    opened = False
    stray = False
    ordinal = 1
    language = ""
    for raw in response.splitlines():
        line = raw.strip()
        label = _GROUP_LABEL_RE.match(line)
        if label is not None:
            ordinal = int(label.group(1))
            named.append(ordinal)
            opened = True
            language = ""
            _answer_parts(sections, ordinal)
            continue
        if not line or _SEPARATOR_RE.match(line):
            continue
        marker = _LANGUAGE_LABEL_RE.match(line)
        if marker is not None:
            # The label settles the rows written before it: the request lists the
            # group's English first, so those rows are its English part.
            _answer_parts(sections, ordinal)[_ENGLISH_LANGUAGE].extend(
                unlabelled.pop(ordinal, [])
            )
            language = marker.group(1)
            continue
        row = _POSITION_RE.match(line)
        if row is None:
            continue
        if not opened:
            stray = True
        anchor = (int(row.group(1)), row.group(2).strip())
        if language:
            _answer_parts(sections, ordinal)[language].append(anchor)
        else:
            unlabelled.setdefault(ordinal, []).append(anchor)
    if stray and group_count > 1:
        return {}
    if len(set(named)) != len(named):
        return {}
    if any(ordinal < 1 or ordinal > group_count for ordinal in named):
        return {}
    for ordinal, rows in unlabelled.items():
        _answer_parts(sections, ordinal)[_CHINESE_LANGUAGE].extend(rows)
    return {
        ordinal: _GroupAnswer(tuple(parts[_ENGLISH_LANGUAGE]), tuple(parts[_CHINESE_LANGUAGE]))
        for ordinal, parts in sections.items()
    }


def _answered_rows(
    rows: Sequence[tuple[int, str]], size: int
) -> list[tuple[int, str]] | None:
    """One part of an answer in the group's block order, or ``None``.

    A part of an answer is only usable when it speaks about every position of its
    group exactly once.  A partial one would mix the rows the model re-placed with
    rows it never answered, and the meaning an unanswered row still carries would
    be doubled next to its replacement.
    """
    if len(rows) != size or sorted(anchor for anchor, _ in rows) != list(range(1, size + 1)):
        return None
    return sorted(rows)


def _complete_chinese(rows: Sequence[tuple[int, str]], size: int) -> tuple[str, ...] | None:
    """One group's answer as its Chinese rows, or ``None`` when it cannot be used.

    An answer that empties the whole group is refused as well: the repair moves
    meaning inside the group, it never removes it.
    """
    answered = _answered_rows(rows, size)
    if answered is None:
        return None
    texts = [""] * size
    for anchor, row in answered:
        texts[anchor - 1] = "" if _is_chinese_gap(row) else row
    if not any(texts):
        return None
    return tuple(texts)


def _strip_timecode(row: str) -> str:
    """Drop a timecode range a candidate wrote into its English row.

    Times are never taken from a candidate — the module computes them from the
    group's pre-repair positions — so a range the model adds to be helpful is
    noise around the words, not part of the English.  What is left is compared
    word for word like every other candidate row.
    """
    return _TIMECODE_RE.sub(" ", row).strip()


def _complete_english(rows: Sequence[tuple[int, str]], size: int) -> tuple[str, ...] | None:
    """One group's re-partitioned English, or ``None`` when it cannot be used.

    The group has to come back with one row per subtitle block, in the block's own
    order, and every row has to hold English: an answer that answers a position
    twice, leaves one out or leaves a block empty would change the subtitle's
    block structure instead of moving where its split falls.
    """
    answered = _answered_rows(rows, size)
    if answered is None:
        return None
    texts = tuple(_strip_timecode(row) for _, row in answered)
    if not all(texts):
        return None
    return texts


def _group_estimation_base(
    blocks: Sequence[SubtitleBlock], indices: Sequence[int]
) -> tuple[list[int], list[tuple[int, int]]]:
    """A group's pre-repair English as a character->timecode map and its word spans.

    Follows the punctuation stage's own way of estimating times from character
    positions (``worker/scripts/resegment.py``): a block's characters are spread
    evenly over that block's span, the position after its last character lands
    exactly on its end time, and the single space that joins two blocks lands on
    the earlier block's end time.  Only the group's own blocks are read, and they
    are read exactly as the repair received them, so every candidate is estimated
    from the pre-repair positions and timecodes and no estimate can drift with the
    result of an earlier one.
    """
    rows = [blocks[index].english for index in indices]
    text = " ".join(rows)
    char_ms = [0] * (len(text) + 1)
    position = 0
    for offset, (row, index) in enumerate(zip(rows, indices)):
        block = blocks[index]
        length = len(row)
        if length:
            for step in range(length):
                char_ms[position + step] = block.start_ms + int(
                    (step / length) * (block.end_ms - block.start_ms)
                )
            char_ms[position + length] = block.end_ms
        else:
            char_ms[position] = block.end_ms
        position += length
        if offset < len(rows) - 1:
            char_ms[position] = block.end_ms
            position += 1
    return char_ms, [(match.start(), match.end()) for match in _WORD_RE.finditer(text)]


def _estimate_group_times(
    char_ms: Sequence[int], spans: Sequence[tuple[int, int]], rows: Sequence[str]
) -> tuple[tuple[int, int], ...] | None:
    """Timecodes for a candidate partition of a group's words, from the base map.

    A row only ever holds words the group already had, so it keeps the character
    span of those words and its boundaries are read straight off the pre-repair
    map: one row ends where the words it holds end, and the next starts where its
    own first word starts.  ``None`` means the rows do not add up to the group's
    words at all.
    """
    times: list[tuple[int, int]] = []
    cursor = 0
    for row in rows:
        length = len(row.split())
        if not length or cursor + length > len(spans):
            return None
        times.append((char_ms[spans[cursor][0]], char_ms[spans[cursor + length - 1][1]]))
        cursor += length
    if cursor != len(spans):
        return None
    return tuple(times)


def _group_candidate(
    blocks: Sequence[SubtitleBlock], group: SentenceGroup, answer: _GroupAnswer
) -> tuple[SubtitleBlock, ...] | None:
    """The group's blocks as this answer would replace them, or ``None``.

    The Chinese rows have to answer every position of the group: the repair
    reallocates what is already translated, it never leaves a group half answered.
    An answer that also re-partitions the group's English has to keep every word,
    in order, with its punctuation, in one non-empty row per block, and the new
    split is then given timecodes estimated from the group's pre-repair positions
    and timecodes — the group keeps its own start and end, and each of its blocks
    keeps a positive duration without a new overlap.

    Anything else — a changed, dropped, duplicated or reordered word, changed
    punctuation, an empty English row, content the group does not have, or an
    estimate that cannot keep the group legal — makes the whole answer unusable,
    so the group keeps its own English, Chinese and timecodes together.
    """
    indices = group.block_indices
    chinese = _complete_chinese(answer.chinese, len(indices))
    if chinese is None:
        return None
    source_rows = [blocks[index].english for index in indices]
    unchanged = tuple(
        replace(blocks[index], chinese=text) for index, text in zip(indices, chinese)
    )
    if not answer.english:
        return unchanged
    english = _complete_english(answer.english, len(indices))
    if english is None or validate_english_preserved(source_rows, english) is not None:
        return None
    moved = [row.split() != source.split() for row, source in zip(english, source_rows)]
    if not any(moved):
        return unchanged
    char_ms, spans = _group_estimation_base(blocks, indices)
    times = _estimate_group_times(char_ms, spans, english)
    if times is None:
        return None
    candidate = tuple(
        replace(
            blocks[index],
            start_ms=start,
            end_ms=end,
            text_lines=(row,) if changed else blocks[index].text_lines,
            chinese=text,
        )
        for index, row, (start, end), changed, text in zip(
            indices, english, times, moved, chinese
        )
    )
    if validate_time_legality(candidate) is not None:
        return None
    # The estimate may only re-cut the group's own span, so its first block still
    # starts where the group started and its last block still ends where the group
    # ended; and because it never introduces an overlap between the group's own
    # blocks, no subtitle outside the group can be overlapped by it either.
    if (
        candidate[0].start_ms != blocks[indices[0]].start_ms
        or candidate[-1].end_ms != blocks[indices[-1]].end_ms
    ):
        return None
    if any(
        later.start_ms < earlier.end_ms
        for earlier, later in zip(candidate, candidate[1:])
    ):
        return None
    return candidate


def _repair_alignment(
    blocks: Sequence[SubtitleBlock],
    spans: Sequence[tuple[int, int]],
    model_call: ModelCall,
) -> tuple[SubtitleBlock, ...]:
    """Repair the sentence groups that still hold a Chinese gap.

    A sentence group belongs to the Translation Chunk that holds its first block,
    and each such chunk is asked once, carrying all of its triggered groups, so
    the number of requests follows the chunks, not the gaps.  An answer is applied
    by whole group: the group whose answer is missing or unusable keeps its own
    English, Chinese and timecodes, so a response can never be half applied.  Every
    candidate is read against ``blocks`` — the subtitle as the repair found it —
    so a group's new times are estimated from the pre-repair positions and
    timecodes, never from what an earlier group's answer changed.

    The repair is an optional improvement and is never tried again: a request that
    fails, like an answer that cannot be read, leaves that request scope — the
    triggered groups of that chunk — with the result they already carry, and the
    subtitle is delivered from there.
    """
    groups = rebuild_sentence_groups(blocks)
    repaired = list(blocks)
    for start, end in spans:
        triggered = [
            group
            for group in groups
            if start <= group.block_indices[0] < end
            and _group_with_a_chinese_gap(blocks, group)
        ]
        if not triggered:
            continue
        try:
            sections = _read_repair_rows(
                model_call(_repair_request(blocks, triggered)), len(triggered)
            )
        except Exception:
            continue
        for ordinal, group in enumerate(triggered, 1):
            candidate = _group_candidate(blocks, group, sections.get(ordinal, _NO_ANSWER))
            if candidate is None:
                continue
            trial = list(repaired)
            for index, block in zip(group.block_indices, candidate):
                trial[index] = block
            # A moved split may not re-cut the subtitle's sentence groups: the
            # group has to stay the group the translator worked on.
            if rebuild_sentence_groups(trial) != groups:
                continue
            repaired = trial
    return tuple(repaired)


def generate_bilingual_srt(
    srt_path: Path,
    chinese_lines: Sequence[str],
    *,
    english_rows: Sequence[str] | None = None,
    model_call: ModelCall | None = None,
) -> BilingualSrtResult:
    """Deliver the Bilingual SRT for one pre-repair timed English subtitle.

    ``chinese_lines`` holds one row per subtitle block with a blank row where the
    translation left a Chinese gap.  ``english_rows`` is the flat English product
    the Translation Chunks were built from; when given, the subtitle's own English
    has to agree with it word for word.  ``model_call`` is the injected external
    model dependency the local alignment repair uses: it repairs the sentence
    groups that still hold a Chinese gap, one request per Translation Chunk, by
    reallocating their Chinese and — when a split is worth moving — by moving where
    one of them is cut, which the module gives timecodes estimated from the
    pre-repair positions.  Any answer it cannot use leaves the group's own
    English, Chinese and timecodes in place.  It is asked once per affected chunk
    and never retried: a request that fails, like an answer whose rows cannot be
    attributed, leaves that request scope with the result it already has — which is
    why an optional repair cannot fail a run that was otherwise usable.  Without
    that dependency the repair does not run at all.  Returns the written artifact,
    or a failure of the subtitle input or of the write, which left the original
    subtitle input untouched.
    """
    srt_path = Path(srt_path)
    try:
        blocks = parse_timed_english_srt(srt_path.read_text(encoding="utf-8-sig"))
    except (OSError, SrtInputError) as exc:
        return _failure(srt_path, str(exc))

    error = validate_block_count(blocks, chinese_lines, english_rows)
    if error is not None:
        return _failure(srt_path, error)

    delivered = tuple(
        replace(block, chinese=_clean_row(row)) for block, row in zip(blocks, chinese_lines)
    )

    error = validate_group_ranges(rebuild_sentence_groups(delivered), len(delivered))
    if error is not None:
        return _failure(srt_path, error)

    error = validate_time_legality(delivered)
    if error is not None:
        return _failure(srt_path, error)

    source_english = [block.english for block in blocks]
    if english_rows is not None:
        error = validate_english_preserved(english_rows, source_english)
        if error is not None:
            return _failure(srt_path, error)

    if model_call is not None:
        spans = _translation_chunk_spans(srt_path, len(delivered))
        if spans is not None:
            # A candidate may reallocate Chinese and move a group's English split,
            # so the block count, the English content, the sentence groups and the
            # timecodes are all checked again below before anything is written.
            delivered = _repair_alignment(delivered, spans, model_call)

    error = validate_english_preserved(source_english, [block.english for block in delivered])
    if error is not None:
        return _failure(srt_path, error)

    error = validate_group_ranges(rebuild_sentence_groups(delivered), len(delivered))
    if error is not None:
        return _failure(srt_path, error)

    error = validate_time_legality(delivered)
    if error is not None:
        return _failure(srt_path, error)

    output_path = srt_path.parent / f"Bilingual_{srt_path.name}"
    try:
        _write_artifact(output_path, render_bilingual_srt(delivered))
    except OSError as exc:
        return BilingualSrtResult(False, None, f"cannot write {output_path.name}: {exc}")

    return BilingualSrtResult(True, output_path, "")


def _write_artifact(output_path: Path, text: str) -> None:
    """Write the finished artifact in one move, so no half-built file survives."""
    staging = output_path.with_name(f"{output_path.name}.partial")
    with open(staging, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    staging.replace(output_path)
