# © Copyright 2026 Aaron Kimball
"""Core mechanics (read, edit, create, search, diff, secret redaction) for tool pairs."""

from klorb.tools.util.create_file_core import CreateFileCore
from klorb.tools.util.diff_lines import DIFF_CONTEXT_LINES, DiffHunk, DiffLine, build_diff_hunks
from klorb.tools.util.dir_walk import walk_readable_tree
from klorb.tools.util.edit_file_core import EditFileCore, LineRangeEdit
from klorb.tools.util.read_file_core import (
    READ_PREVIEW_MAX_LINES,
    FullFileView,
    ReadFileCore,
    format_read_result,
    parse_numbered_content,
    read_full_file_lines,
)
from klorb.tools.util.search_core import (
    VALID_OUTPUT_STYLES,
    compile_queries,
    context_lines_for_matches,
    format_match_line,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
)
from klorb.tools.util.secret_redaction import (
    SECRET_DETECTION_PLUGINS,
    SECRET_DETECTION_SCAN_LOCK,
    SecretRedactor,
    clear_cached_redactor,
    get_or_create_secret_redactor,
    load_secrets_baseline,
)
from klorb.tools.util.semantic_search_core import HybridSearchable, SemanticSearchCore
from klorb.tools.util.spill import SpillDir

__all__ = [
    "CreateFileCore",
    "DIFF_CONTEXT_LINES",
    "DiffHunk",
    "DiffLine",
    "EditFileCore",
    "FullFileView",
    "HybridSearchable",
    "LineRangeEdit",
    "READ_PREVIEW_MAX_LINES",
    "ReadFileCore",
    "SECRET_DETECTION_PLUGINS",
    "SECRET_DETECTION_SCAN_LOCK",
    "SecretRedactor",
    "SemanticSearchCore",
    "SpillDir",
    "VALID_OUTPUT_STYLES",
    "build_diff_hunks",
    "clear_cached_redactor",
    "compile_queries",
    "context_lines_for_matches",
    "format_match_line",
    "format_read_result",
    "get_or_create_secret_redactor",
    "load_secrets_baseline",
    "match_line_indices",
    "matches_only",
    "parse_numbered_content",
    "read_full_file_lines",
    "validate_output_style",
    "validate_queries",
    "walk_readable_tree",
]
