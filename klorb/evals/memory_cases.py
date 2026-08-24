# © Copyright 2026 Aaron Kimball
"""EvalCase tasks covering the Memory tools and the MEMORY.md table of contents, grouped into the
"memory" EvalSuite. See docs/specs/memories.md.
"""

from pathlib import Path

import klorb.tools.memory.common as memory_common_module
from klorb.session import Session
from klorb.tools.memory.common import (
    MEMORIES_DIRNAME,
    MEMORY_TOC_AUTO_READ_LINES,
    MEMORY_TOC_FILENAME,
    MEMORY_TOC_WARN_LINES,
)

from .harness import EvalCase, EvalSuite


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _lines(path: Path) -> list[str]:
    return _read(path).splitlines()


_DEPLOY_KEY_FINGERPRINT = "SHA256:aB3xQ9zR7k2mFp8"

_INTERJECTION_WORKSPACE_TOC = (
    "Workspace notes table of contents\n\n"
    "## Deployment\n"
    f"Staging deploy key fingerprint: {_DEPLOY_KEY_FINGERPRINT}\n"
    "See deploy-notes.md for the full runbook.\n"
)


def _check_memory_toc_interjection_answers_without_reread(
    workspace_root: Path, session: Session,
) -> str | None:
    path = workspace_root / "answer.txt"
    if not path.exists():
        return "answer.txt was not created"
    content = _read(path).strip()
    if content != _DEPLOY_KEY_FINGERPRINT:
        return f"answer.txt should contain exactly {_DEPLOY_KEY_FINGERPRINT!r}, got {content!r}"
    return None


MEMORY_TOC_INTERJECTION_ANSWERS_WITHOUT_REREAD = EvalCase(
    name="memory_toc_interjection_answers_without_reread",
    prompt=(
        "What is the staging deploy key fingerprint recorded in your workspace memories? Write "
        "just that fingerprint (starting with 'SHA256:') into answer.txt, nothing else."
    ),
    setup_files={f".klorb/memories/{MEMORY_TOC_FILENAME}": _INTERJECTION_WORKSPACE_TOC},
    workspace_trusted=True,
    check=_check_memory_toc_interjection_answers_without_reread,
    # The fingerprint is already in the first-turn Memories interjection, so a clean run needs
    # only CreateFile.
    expected_tool_calls=1,
)


_LEGACY_ARCHIVE_CODE = "LEGACY-9F2K"
_TRUNCATION_TOC_TOTAL_LINES = 60
_TRUNCATION_MARKER_LINE = MEMORY_TOC_AUTO_READ_LINES + 5
"""Past `MEMORY_TOC_AUTO_READ_LINES`, so the marker isn't folded into the first-turn
interjection."""


def _build_truncation_toc() -> str:
    lines = ["Workspace notes table of contents"]
    for i in range(2, _TRUNCATION_TOC_TOTAL_LINES + 1):
        if i == _TRUNCATION_MARKER_LINE:
            lines.append(f"Legacy migration archive code: {_LEGACY_ARCHIVE_CODE}")
        else:
            lines.append(f"Filler note {i}")
    return "\n".join(lines) + "\n"


def _check_memory_toc_truncation_prompts_paging(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "answer.txt"
    if not path.exists():
        return "answer.txt was not created"
    content = _read(path).strip()
    if content != _LEGACY_ARCHIVE_CODE:
        return f"answer.txt should contain exactly {_LEGACY_ARCHIVE_CODE!r}, got {content!r}"
    return None


MEMORY_TOC_TRUNCATION_PROMPTS_PAGING = EvalCase(
    name="memory_toc_truncation_prompts_paging",
    prompt=(
        "Your workspace memories mention a legacy migration archive code somewhere. Find it and "
        "write it to answer.txt, and nothing else."
    ),
    setup_files={f".klorb/memories/{MEMORY_TOC_FILENAME}": _build_truncation_toc()},
    workspace_trusted=True,
    check=_check_memory_toc_truncation_prompts_paging,
    # Clean shape: one lookup call (SearchMemories, or a single ReadMemory -- its default
    # max_lines already covers the whole 60-line file) + CreateFile.
    expected_tool_calls=2,
)


_OVERFLOW_FACT_KEYWORD = "Reviewable"


def _build_overflow_preseed_toc() -> str:
    """A global `MEMORY.md` already past `MEMORY_TOC_AUTO_READ_LINES` before the model ever
    edits it, so the resulting overflow warning and required compaction don't hinge on exactly
    how many lines the model's own edit happens to add."""
    lines = ["Global memory table of contents", ""]
    topic = 0
    while len(lines) <= MEMORY_TOC_AUTO_READ_LINES:
        topic += 1
        lines += [
            f"## Topic {topic}", f"- fact about topic {topic}",
            f"- see topic-{topic:02d}.md for detail",
        ]
    return "\n".join(lines) + "\n"


def _check_memory_toc_overflow_warning_triggers_compaction(
    workspace_root: Path, session: Session,
) -> str | None:
    memories_dir = memory_common_module.get_klorb_data_dir() / MEMORIES_DIRNAME
    toc_path = memories_dir / MEMORY_TOC_FILENAME
    if not toc_path.is_file():
        return "global MEMORY.md no longer exists"
    toc_line_count = len(_lines(toc_path))
    if toc_line_count >= MEMORY_TOC_WARN_LINES:
        return (
            f"global MEMORY.md should have been compacted back under {MEMORY_TOC_WARN_LINES} "
            f"lines after the overflow warning, has {toc_line_count}"
        )

    all_content = "\n".join(
        _read(path) for path in memories_dir.iterdir()
        if path.is_file() and path.suffix == ".md"
    )
    if _OVERFLOW_FACT_KEYWORD not in all_content:
        return (
            f"none of the global memory files mention {_OVERFLOW_FACT_KEYWORD!r} -- the new "
            "fact was not recorded anywhere"
        )
    return None


MEMORY_TOC_OVERFLOW_WARNING_TRIGGERS_COMPACTION = EvalCase(
    name="memory_toc_overflow_warning_triggers_compaction",
    prompt=(
        "I want you to remember something about me for every future session, in every project I "
        "work on: I prefer using the code review tool 'Reviewable' over GitHub's own PR review "
        "UI. Make sure this is saved in your memory system so you recall it in later sessions."
    ),
    global_memory_files={MEMORY_TOC_FILENAME: _build_overflow_preseed_toc()},
    check=_check_memory_toc_overflow_warning_triggers_compaction,
    # Clean shape: EditMemory to append, CreateMemory for a subject file, EditMemory to compact
    # MEMORY.md back down.
    expected_tool_calls=3,
)

MEMORY_CASES: list[EvalCase] = [
    MEMORY_TOC_INTERJECTION_ANSWERS_WITHOUT_REREAD,
    MEMORY_TOC_TRUNCATION_PROMPTS_PAGING,
    MEMORY_TOC_OVERFLOW_WARNING_TRIGGERS_COMPACTION,
]

MEMORY = EvalSuite(name="memory", cases=MEMORY_CASES)
