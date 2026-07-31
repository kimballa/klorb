# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session.mixins.mentions -- @mention file inlining."""

from pathlib import Path

import pytest

from klorb.message import MessageFragment
from klorb.session.mixins.mentions import (
    _AT_MENTION_RE,
    has_at_mention,
    resolve_at_mentions,
    unescape_mention_filename,
)
from klorb.tools.util.read_file_core import ReadFileCore

# --- unescape_mention_filename ---


class TestUnescapeMentionFilename:
    """Tests for unescape_mention_filename."""

    def test_no_escapes(self) -> None:
        assert unescape_mention_filename("foo.txt") == "foo.txt"

    def test_escaped_space(self) -> None:
        assert unescape_mention_filename(r"foo\ bar.txt") == "foo bar.txt"

    def test_multiple_escaped_spaces(self) -> None:
        assert unescape_mention_filename(r"foo\ bar\ baz.txt") == "foo bar baz.txt"

    def test_escaped_backslash(self) -> None:
        assert unescape_mention_filename(r"foo\\bar.txt") == r"foo\bar.txt"

    def test_escaped_double_quote(self) -> None:
        assert unescape_mention_filename(r'foo\"bar.txt') == 'foo"bar.txt'

    def test_literal_backslash_before_other_char(self) -> None:
        """A backslash followed by a non-special character is kept as-is."""
        assert unescape_mention_filename(r"abc\def") == r"abc\def"

    def test_mixed_escapes(self) -> None:
        assert unescape_mention_filename(r"foo\ bar\\baz") == r"foo bar\baz"

    def test_empty_string(self) -> None:
        assert unescape_mention_filename("") == ""

    def test_trailing_backslash(self) -> None:
        """A trailing backslash with nothing after it is kept as-is."""
        assert unescape_mention_filename("foo\\") == "foo\\"

    def test_escaped_space_at_start(self) -> None:
        assert unescape_mention_filename(r"\ foo") == " foo"

    def test_windows_path(self) -> None:
        assert unescape_mention_filename(r"c:\\some\\windows\\path.txt") == r"c:\some\windows\path.txt"


# --- _AT_MENTION_RE ---


class TestAtMentionRegex:
    """Tests for the @mention regex pattern."""

    def test_simple_filename(self) -> None:
        m = _AT_MENTION_RE.search("check @foo.txt please")
        assert m is not None
        assert m.group(2) == "foo.txt"

    def test_path_with_directory(self) -> None:
        m = _AT_MENTION_RE.search("see @src/main.py")
        assert m is not None
        assert m.group(2) == "src/main.py"

    def test_escaped_space(self) -> None:
        m = _AT_MENTION_RE.search(r"look at @foo\ bar.txt")
        assert m is not None
        assert m.group(2) == r"foo\ bar.txt"

    def test_quoted_filename(self) -> None:
        m = _AT_MENTION_RE.search('@"foo bar.txt"')
        assert m is not None
        assert m.group(1) == "foo bar.txt"

    def test_quoted_with_escapes(self) -> None:
        m = _AT_MENTION_RE.search(r'@"foo\"bar.txt"')
        assert m is not None
        assert m.group(1) == r'foo\"bar.txt'

    def test_no_match_at_sign_in_word(self) -> None:
        """An @ embedded in a word (e.g. email) is not matched."""
        m = _AT_MENTION_RE.search("user@example.com")
        assert m is None

    def test_at_sign_at_end(self) -> None:
        """A bare @ at the end of the prompt is not matched."""
        m = _AT_MENTION_RE.search("hello @")
        assert m is None

    def test_multiple_mentions(self) -> None:
        matches = list(_AT_MENTION_RE.finditer("@foo.txt @bar.py"))
        assert len(matches) == 2
        assert matches[0].group(2) == "foo.txt"
        assert matches[1].group(2) == "bar.py"

    def test_mention_at_start_of_prompt(self) -> None:
        m = _AT_MENTION_RE.search("@foo.txt is important")
        assert m is not None
        assert m.group(2) == "foo.txt"

    def test_mention_at_end_of_prompt(self) -> None:
        m = _AT_MENTION_RE.search("see @foo.txt")
        assert m is not None
        assert m.group(2) == "foo.txt"


# --- has_at_mention ---


class TestHasAtMention:
    """Tests for has_at_mention."""

    def test_with_mention(self) -> None:
        assert has_at_mention("check @foo.txt") is True

    def test_without_mention(self) -> None:
        assert has_at_mention("no mentions here") is False

    def test_email_not_mention(self) -> None:
        assert has_at_mention("email user@example.com") is False

    def test_empty_string(self) -> None:
        assert has_at_mention("") is False


# --- resolve_at_mentions ---


class TestResolveAtMentions:
    """Tests for resolve_at_mentions with real files."""

    @pytest.fixture
    def core(self) -> ReadFileCore:
        return ReadFileCore(max_lines=200)

    def test_no_mentions_returns_none(self, core: ReadFileCore, tmp_path: Path) -> None:
        assert resolve_at_mentions("hello world", core, tmp_path) is None

    def test_prompt_is_never_modified(self, core: ReadFileCore, tmp_path: Path) -> None:
        """resolve_at_mentions must not mutate or replace the prompt text itself."""
        (tmp_path / "hello.txt").write_text("line one\n")
        prompt = "check @hello.txt please"
        resolve_at_mentions(prompt, core, tmp_path)
        assert prompt == "check @hello.txt please"

    def test_simple_file_fragment(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("line one\nline two\n")
        fragments = resolve_at_mentions("check @hello.txt please", core, tmp_path)
        assert fragments is not None
        assert len(fragments) == 1
        assert fragments[0].type == "text"
        text = fragments[0].text
        assert "Filename: hello.txt" in text
        assert "1|line one" in text
        assert "2|line two" in text
        assert "Total lines: 2" in text
        assert "Truncated: false" in text

    def test_relative_path_resolves_within_workspace(self, core: ReadFileCore, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "a.txt").write_text("content")
        fragments = resolve_at_mentions("see @sub/a.txt", core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Filename: sub/a.txt" in text
        assert "1|content" in text

    def test_absolute_path(self, core: ReadFileCore, tmp_path: Path) -> None:
        target = tmp_path / "abs.txt"
        target.write_text("absolute content")
        fragments = resolve_at_mentions(f"see @{target}", core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Filename:" in text
        assert "1|absolute content" in text

    def test_nonexistent_file_produces_error_note(self, core: ReadFileCore, tmp_path: Path) -> None:
        fragments = resolve_at_mentions("check @nope.txt", core, tmp_path)
        assert fragments is not None
        assert "(error reading file:" in fragments[0].text

    def test_multiple_mentions(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        fragments = resolve_at_mentions("@a.txt and @b.txt", core, tmp_path)
        assert fragments is not None
        assert len(fragments) == 2
        assert "Filename: a.txt" in fragments[0].text
        assert "Attachment Id: 1" in fragments[0].text
        assert "Filename: b.txt" in fragments[1].text
        assert "Attachment Id: 2" in fragments[1].text

    def test_duplicate_mentions_only_read_once(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "dup.txt").write_text("content")
        fragments = resolve_at_mentions("@dup.txt and @dup.txt again", core, tmp_path)
        assert fragments is not None
        # Only one fragment (id=1) should exist, not two.
        assert len(fragments) == 1
        assert "Attachment Id: 1" in fragments[0].text

    def test_escaped_space_in_filename(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "my file.txt").write_text("spaced out")
        fragments = resolve_at_mentions(r"@my\ file.txt", core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Filename: my file.txt" in text
        assert "1|spaced out" in text

    def test_quoted_filename_with_spaces(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "my file.txt").write_text("quoted content")
        fragments = resolve_at_mentions('@"my file.txt"', core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Filename: my file.txt" in text
        assert "1|quoted content" in text

    def test_truncation(self, core: ReadFileCore, tmp_path: Path) -> None:
        """Files larger than max_lines are marked as truncated."""
        small_core = ReadFileCore(max_lines=3)
        lines = "\n".join(f"line {i}" for i in range(1, 6))
        (tmp_path / "big.txt").write_text(lines)
        fragments = resolve_at_mentions("@big.txt", small_core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Truncated: true" in text
        assert "Total lines: 5" in text

    def test_attachment_ordinal_format(self, core: ReadFileCore, tmp_path: Path) -> None:
        """Attachment blocks include all expected metadata fields."""
        (tmp_path / "f.txt").write_text("hello")
        fragments = resolve_at_mentions("@f.txt", core, tmp_path)
        assert fragments is not None
        text = fragments[0].text
        assert "Filename: f.txt\n" in text
        assert "Attachment Id: 1\n" in text
        assert "Total lines: 1\n" in text
        assert "Truncated: false\n" in text

    def test_fragments_are_message_fragment_instances(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("data")
        fragments = resolve_at_mentions("before @f.txt after", core, tmp_path)
        assert fragments is not None
        assert all(isinstance(fragment, MessageFragment) for fragment in fragments)
