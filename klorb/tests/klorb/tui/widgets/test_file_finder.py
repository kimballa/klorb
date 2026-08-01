# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.file_finder."""

from klorb.tui.widgets.file_finder import (
    MentionQuery,
    build_mention_insertion,
    detect_mention_query,
    escape_mention_path,
    filter_workspace_files,
    split_finder_row,
)


class TestDetectMentionQuery:
    def test_finds_mention_at_start_of_line(self) -> None:
        line = "@foo"
        assert detect_mention_query(line, len(line)) == MentionQuery(start_column=0, query="foo")

    def test_finds_mention_after_whitespace(self) -> None:
        line = "check @src/main"
        assert detect_mention_query(line, len(line)) == MentionQuery(
            start_column=6, query="src/main")

    def test_query_is_empty_right_after_the_at_sign(self) -> None:
        line = "hello @"
        assert detect_mention_query(line, len(line)) == MentionQuery(start_column=6, query="")

    def test_no_mention_without_an_at_sign(self) -> None:
        line = "just plain text"
        assert detect_mention_query(line, len(line)) is None

    def test_whitespace_before_cursor_ends_the_mention(self) -> None:
        line = "@foo bar"
        assert detect_mention_query(line, len(line)) is None

    def test_at_sign_embedded_in_a_word_does_not_trigger(self) -> None:
        line = "user@example.com"
        assert detect_mention_query(line, len(line)) is None

    def test_cursor_before_the_at_sign_finds_nothing(self) -> None:
        assert detect_mention_query("@foo", 0) is None


class TestFilterWorkspaceFiles:
    def test_empty_query_returns_sorted_paths_up_to_limit(self) -> None:
        paths = ["zz.py", "aa.py", "mm.py"]

        assert filter_workspace_files(paths, "", limit=2) == ["aa.py", "mm.py"]

    def test_query_ranks_by_fuzzy_match_score(self) -> None:
        paths = ["src/main.py", "src/other.py", "unrelated.txt"]

        matches = filter_workspace_files(paths, "main")

        assert matches[0] == "src/main.py"
        assert "unrelated.txt" not in matches

    def test_respects_limit(self) -> None:
        paths = [f"file{i}.py" for i in range(10)]

        assert len(filter_workspace_files(paths, "file", limit=3)) == 3


class TestEscapeMentionPath:
    def test_escapes_spaces(self) -> None:
        assert escape_mention_path("foo bar.txt") == "foo\\ bar.txt"

    def test_escapes_double_quotes(self) -> None:
        assert escape_mention_path('foo"bar.txt') == 'foo\\"bar.txt'

    def test_escapes_backslashes(self) -> None:
        assert escape_mention_path("foo\\bar.txt") == "foo\\\\bar.txt"

    def test_escapes_backslash_before_other_escapes_so_they_are_not_double_escaped(self) -> None:
        # A literal backslash-space in the path must come out as `\\` + `\ ` (three chars: `\`,
        # `\`, `\`, ` `... i.e. backslash escaped first, then the resulting space escaped too),
        # not re-escaped into something else.
        assert escape_mention_path("a\\ b.txt") == "a\\\\\\ b.txt"


class TestBuildMentionInsertion:
    def test_builds_escaped_mention_with_trailing_space(self) -> None:
        insertion, new_column = build_mention_insertion(6, 10, "foo bar.txt")

        assert insertion == "@foo\\ bar.txt "
        assert new_column == 6 + len(insertion)


class TestSplitFinderRow:
    def test_no_directory_component(self) -> None:
        assert split_finder_row("file.txt", 80) == ("", "file.txt")

    def test_fits_within_available_width_unchanged(self) -> None:
        assert split_finder_row("a/b/c.txt", 80) == ("a/b", "/c.txt")

    def test_unknown_width_never_truncates(self) -> None:
        long_path = "a/" * 50 + "file.txt"
        assert split_finder_row(long_path, 0) == (long_path[:-9], "/file.txt")

    def test_truncates_the_end_of_the_dir_part_when_it_does_not_fit(self) -> None:
        dir_display, file_display = split_finder_row("foo/bar/baz/quux/deep/path/someFile.txt", 30)

        assert file_display == "/someFile.txt"
        assert dir_display.endswith("...")
        assert len(dir_display) + len(file_display) <= 30

    def test_extremely_narrow_width_still_shows_the_full_file_part(self) -> None:
        dir_display, file_display = split_finder_row("a/b/c/d/e.txt", 3)

        assert file_display == "/e.txt"
        assert dir_display == "..."
