# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.file_finder."""

from klorb.tui.widgets.file_finder import (
    FinderMatch,
    MentionQuery,
    _ancestor_directories,
    _split_query_directory,
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


class TestAncestorDirectories:
    def test_no_directories_for_flat_paths(self) -> None:
        assert _ancestor_directories(["a.py", "b.py"]) == set()

    def test_collects_every_ancestor_of_a_nested_path(self) -> None:
        assert _ancestor_directories(["a/b/c.py"]) == {"a", "a/b"}

    def test_dedupes_shared_ancestors_across_paths(self) -> None:
        assert _ancestor_directories(["a/b/c.py", "a/b/d.py", "a/e.py"]) == {"a", "a/b"}


class TestSplitQueryDirectory:
    def test_no_slash_is_all_fragment(self) -> None:
        assert _split_query_directory("main") == ("", "main")

    def test_trailing_slash_leaves_an_empty_fragment(self) -> None:
        assert _split_query_directory("klorb/") == ("klorb/", "")

    def test_splits_at_the_last_slash(self) -> None:
        assert _split_query_directory("klorb/sr") == ("klorb/", "sr")


class TestFilterWorkspaceFiles:
    def test_empty_query_returns_sorted_paths(self) -> None:
        paths = ["zz.py", "aa.py", "mm.py"]

        assert filter_workspace_files(paths, "") == [
            FinderMatch("aa.py", is_dir=False), FinderMatch("mm.py", is_dir=False),
            FinderMatch("zz.py", is_dir=False)]

    def test_empty_query_never_truncates_the_immediate_contents_of_the_current_directory(
        self,
    ) -> None:
        paths = [f"file{i}.py" for i in range(30)]

        matches = filter_workspace_files(paths, "", limit=5)

        assert len(matches) == 30

    def test_empty_query_sorts_directories_ahead_of_files(self) -> None:
        paths = ["b/file.py", "a_file.py"]

        matches = filter_workspace_files(paths, "", limit=10)

        assert matches == [
            FinderMatch("b", is_dir=True), FinderMatch("a_file.py", is_dir=False),
            FinderMatch("b/file.py", is_dir=False)]

    def test_empty_query_lists_directories_breadth_first_not_alphabetically(self) -> None:
        paths = [".claude/skills/foo.py", ".github/workflows/ci.yml"]

        matches = filter_workspace_files(paths, "", limit=10)

        dirs = [match.path for match in matches if match.is_dir]
        assert dirs == [".claude", ".github", ".claude/skills", ".github/workflows"]

    def test_query_ranks_by_fuzzy_match_score(self) -> None:
        paths = ["src/main.py", "src/other.py", "unrelated.txt"]

        matches = filter_workspace_files(paths, "main")

        assert matches[0] == FinderMatch("src/main.py", is_dir=False)
        assert "unrelated.txt" not in [match.path for match in matches]

    def test_respects_limit(self) -> None:
        paths = [f"file{i}.py" for i in range(10)]

        assert len(filter_workspace_files(paths, "file", limit=3)) == 3

    def test_matching_directory_outranks_an_equally_scored_file(self) -> None:
        paths = ["srcstuff.py", "src/file.py"]

        matches = filter_workspace_files(paths, "src")

        assert matches[0] == FinderMatch("src", is_dir=True)

    def test_non_matching_directory_is_not_included_via_the_bump(self) -> None:
        paths = ["zzz/main.py"]

        matches = filter_workspace_files(paths, "main")

        assert matches == [FinderMatch("zzz/main.py", is_dir=False)]

    def test_shallower_directory_outranks_a_deeper_equally_matching_one(self) -> None:
        paths = ["a/utils/x.py", "a/b/c/utils/y.py"]

        matches = filter_workspace_files(paths, "utils")

        dirs = [match.path for match in matches if match.is_dir]
        assert dirs.index("a/utils") < dirs.index("a/b/c/utils")

    def test_narrowing_into_a_directory_excludes_a_lookalike_outside_it(self) -> None:
        paths = ["klorb/src/app.py", ".klorb/skills/foo.py"]

        matches = filter_workspace_files(paths, "klorb/")

        assert ".klorb/skills" not in [match.path for match in matches]
        assert ".klorb/skills/foo.py" not in [match.path for match in matches]

    def test_narrowing_into_a_directory_surfaces_its_immediate_children_first(self) -> None:
        paths = ["klorb/src/klorb/app.py", "klorb/tests/test_app.py"]

        matches = filter_workspace_files(paths, "klorb/")

        dirs = [match.path for match in matches if match.is_dir]
        assert dirs[:2] == ["klorb/src", "klorb/tests"]

    def test_scoped_browsing_never_truncates_the_current_directorys_own_contents(self) -> None:
        paths = ["klorb/a.py"] + [f"klorb/sub{i}/deep.py" for i in range(30)]

        matches = filter_workspace_files(paths, "klorb/", limit=5)

        immediate = {"klorb/a.py"} | {f"klorb/sub{i}" for i in range(30)}
        assert immediate <= {match.path for match in matches}

    def test_scoped_browsing_fills_remaining_slots_with_deeper_entries_up_to_the_limit(
        self,
    ) -> None:
        paths = ["klorb/a.py"] + [f"klorb/sub/deep{i}.py" for i in range(30)]

        matches = filter_workspace_files(paths, "klorb/", limit=5)

        # klorb/sub (immediate dir) + klorb/a.py (immediate file) + 3 deeper entries to fill to 5.
        assert len(matches) == 5
        assert matches[:2] == [FinderMatch("klorb/sub", is_dir=True), FinderMatch("klorb/a.py", is_dir=False)]

    def test_scoped_browsing_orders_immediate_subdirs_before_immediate_files_before_deeper(
        self,
    ) -> None:
        paths = ["klorb/a.py", "klorb/sub/deep.py"]

        matches = filter_workspace_files(paths, "klorb/")

        assert [match.path for match in matches] == ["klorb/sub", "klorb/a.py", "klorb/sub/deep.py"]


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

    def test_uses_quoted_form_when_filename_ends_in_a_period(self) -> None:
        insertion, new_column = build_mention_insertion(0, 0, "notes.")

        assert insertion == '@"notes." '
        assert new_column == len(insertion)

    def test_uses_quoted_form_when_filename_ends_in_an_exclamation_point(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, "TODO!")

        assert insertion == '@"TODO!" '

    def test_uses_quoted_form_when_filename_ends_in_a_closing_paren(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, "output(1)")

        assert insertion == '@"output(1)" '

    def test_uses_quoted_form_when_filename_ends_in_a_colon(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, "notes:")

        assert insertion == '@"notes:" '

    def test_uses_quoted_form_when_filename_ends_in_a_semicolon(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, "notes;")

        assert insertion == '@"notes;" '

    def test_quoted_form_escapes_backslashes_and_double_quotes(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, 'a\\b "c"!')

        assert insertion == '@"a\\\\b \\"c\\"!" '

    def test_unquoted_form_used_when_filename_does_not_end_in_trailing_punctuation(self) -> None:
        insertion, _ = build_mention_insertion(0, 0, "foo.txt")

        assert insertion == "@foo.txt "


class TestSplitFinderRow:
    def test_no_directory_component(self) -> None:
        assert split_finder_row("file.txt", 80) == ("", "file.txt")

    def test_fits_within_available_width_unchanged(self) -> None:
        assert split_finder_row("a/b/c.txt", 80) == ("a/b", "/c.txt")

    def test_unknown_width_never_truncates(self) -> None:
        long_path = "a/" * 50 + "file.txt"
        assert split_finder_row(long_path, 0) == (long_path[:-9], "/file.txt")

    def test_truncates_the_front_of_the_dir_part_when_it_does_not_fit(self) -> None:
        dir_display, file_display = split_finder_row("foo/bar/baz/quux/deep/path/someFile.txt", 30)

        assert file_display == "/someFile.txt"
        assert dir_display.startswith("..")
        assert dir_display.endswith("path")
        assert len(dir_display) + len(file_display) <= 30

    def test_extremely_narrow_width_still_shows_the_full_file_part(self) -> None:
        dir_display, file_display = split_finder_row("a/b/c/d/e.txt", 3)

        assert file_display == "/e.txt"
        assert dir_display == ".."
