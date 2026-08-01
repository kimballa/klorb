# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session.mixins.mentions -- @mention file inlining."""

import io
from pathlib import Path

import pytest
from PIL import Image

from klorb.images.prepare import ImagePipelineConfig
from klorb.message import MessageFragment
from klorb.models.configured_model import ConfiguredModel
from klorb.session.mixins.mentions import (
    _AT_MENTION_RE,
    detect_mention_mime_type,
    has_at_mention,
    resolve_at_mentions,
    unescape_mention_filename,
)
from klorb.tools.util.read_file_core import ReadFileCore


def _png_bytes(width: int = 20, height: int = 10) -> bytes:
    image = Image.new("RGB", (width, height), (200, 50, 50))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pipeline_config(max_bytes_raw: int = 26214400) -> ImagePipelineConfig:
    return ImagePipelineConfig(
        default_max_dimension_px=1568, max_bytes_raw=max_bytes_raw,
        preferred_formats=["image/webp", "image/png"])


def _vision_model() -> ConfiguredModel:
    return ConfiguredModel(
        {"name": "test/vision-model", "capabilities": {"vision": True}}, source="test")


def _text_only_model() -> ConfiguredModel:
    return ConfiguredModel(
        {"name": "test/text-model", "capabilities": {"vision": False}}, source="test")


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


# --- detect_mention_mime_type ---


class TestDetectMentionMimeType:
    """Tests for detect_mention_mime_type -- magic-byte and extension-based image detection."""

    def test_png_magic_bytes(self) -> None:
        assert detect_mention_mime_type("whatever", _png_bytes()) == "image/png"

    def test_jpeg_magic_bytes(self) -> None:
        head = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        assert detect_mention_mime_type("whatever", head) == "image/jpeg"

    def test_gif87a_magic_bytes(self) -> None:
        assert detect_mention_mime_type("whatever", b"GIF87a rest of file") == "image/gif"

    def test_gif89a_magic_bytes(self) -> None:
        assert detect_mention_mime_type("whatever", b"GIF89a rest of file") == "image/gif"

    def test_bmp_magic_bytes(self) -> None:
        assert detect_mention_mime_type("whatever", b"BMxxxxxxxxxxxx") == "image/bmp"

    def test_webp_magic_bytes(self) -> None:
        head = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 "
        assert detect_mention_mime_type("whatever", head) == "image/webp"

    def test_riff_without_webp_is_not_matched(self) -> None:
        """A RIFF container that isn't WEBP (e.g. a WAV file) is not treated as an image."""
        head = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"fmt "
        assert detect_mention_mime_type("audio.wav", head) is None

    def test_extension_fallback_when_magic_bytes_inconclusive(self) -> None:
        """A mismatched or truncated header still resolves via the file extension."""
        assert detect_mention_mime_type("photo.png", b"not really a png") == "image/png"

    def test_unrecognized_extension_and_bytes_return_none(self) -> None:
        assert detect_mention_mime_type("notes.txt", b"just some text") is None

    def test_heic_extension_not_recognized(self) -> None:
        """HEIC is declared as vision-supported by some models, but this module can neither
        magic-sniff nor decode it, so it must not be treated as a recognized image mention."""
        assert detect_mention_mime_type("photo.heic", b"\x00\x00\x00\x18ftypheic") is None

    def test_empty_bytes_and_no_extension(self) -> None:
        assert detect_mention_mime_type("noext", b"") is None


# --- resolve_at_mentions ---


class TestResolveAtMentions:
    """Tests for resolve_at_mentions with real files."""

    @pytest.fixture
    def core(self) -> ReadFileCore:
        return ReadFileCore(max_lines=200, max_line_length=500)

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
        small_core = ReadFileCore(max_lines=3, max_line_length=500)
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

    def test_image_mention_without_model_falls_back_to_read_error(
        self, core: ReadFileCore, tmp_path: Path,
    ) -> None:
        """With no active_model/image_pipeline_config given (the default), an image mention is
        still attempted as a text read and fails -- unchanged legacy behavior."""
        (tmp_path / "pic.png").write_bytes(_png_bytes())
        fragments = resolve_at_mentions("see @pic.png", core, tmp_path)
        assert fragments is not None
        assert len(fragments) == 1
        assert "(error reading file:" in fragments[0].text


# --- resolve_at_mentions: image mentions ---


class TestResolveAtMentionsImages:
    """Tests for resolve_at_mentions's image-mention path (PNG/JPEG/GIF/WEBP/BMP recognized via
    detect_mention_mime_type, resized/transcoded via klorb.images.prepare.prepare_image_for_model)."""

    @pytest.fixture
    def core(self) -> ReadFileCore:
        return ReadFileCore(max_lines=200, max_line_length=500)

    def test_image_mention_attaches_image_url_fragment(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "pic.png").write_bytes(_png_bytes(40, 20))
        fragments = resolve_at_mentions(
            "see @pic.png", core, tmp_path,
            active_model=_vision_model(), image_pipeline_config=_pipeline_config())
        assert fragments is not None
        assert len(fragments) == 2
        header, image_fragment = fragments
        assert header.type == "text"
        assert "Filename: pic.png" in header.text
        assert "Attachment Id: 1" in header.text
        assert image_fragment.type == "image_url"
        assert image_fragment.image_url is not None
        assert image_fragment.image_url["url"].startswith("data:image/")
        assert image_fragment.source_filename == "pic.png"
        assert image_fragment.original_width == 40
        assert image_fragment.original_height == 20

    def test_image_mention_without_vision_capability_produces_error_fragment(
        self, core: ReadFileCore, tmp_path: Path,
    ) -> None:
        (tmp_path / "pic.png").write_bytes(_png_bytes())
        fragments = resolve_at_mentions(
            "see @pic.png", core, tmp_path,
            active_model=_text_only_model(), image_pipeline_config=_pipeline_config())
        assert fragments is not None
        assert len(fragments) == 1
        assert "does not support image input" in fragments[0].text

    def test_image_mention_with_no_pipeline_config_produces_error_fragment(
        self, core: ReadFileCore, tmp_path: Path,
    ) -> None:
        (tmp_path / "pic.png").write_bytes(_png_bytes())
        fragments = resolve_at_mentions(
            "see @pic.png", core, tmp_path, active_model=_vision_model())
        assert fragments is not None
        assert len(fragments) == 1
        assert "does not support image input" in fragments[0].text

    def test_oversized_image_produces_error_fragment(self, core: ReadFileCore, tmp_path: Path) -> None:
        (tmp_path / "pic.png").write_bytes(_png_bytes(400, 400))
        fragments = resolve_at_mentions(
            "see @pic.png", core, tmp_path,
            active_model=_vision_model(), image_pipeline_config=_pipeline_config(max_bytes_raw=1))
        assert fragments is not None
        assert len(fragments) == 1
        assert "(error reading file:" in fragments[0].text

    def test_corrupt_image_bytes_produce_error_fragment(self, core: ReadFileCore, tmp_path: Path) -> None:
        """A file whose extension claims an image format but whose bytes Pillow can't decode
        fails gracefully instead of raising."""
        (tmp_path / "broken.png").write_bytes(b"not actually a png")
        fragments = resolve_at_mentions(
            "see @broken.png", core, tmp_path,
            active_model=_vision_model(), image_pipeline_config=_pipeline_config())
        assert fragments is not None
        assert len(fragments) == 1
        assert "(error reading file:" in fragments[0].text

    def test_nonexistent_image_mention_produces_error_fragment(
        self, core: ReadFileCore, tmp_path: Path,
    ) -> None:
        fragments = resolve_at_mentions(
            "see @missing.png", core, tmp_path,
            active_model=_vision_model(), image_pipeline_config=_pipeline_config())
        assert fragments is not None
        assert len(fragments) == 1
        assert "(error reading file:" in fragments[0].text

    def test_mixed_text_and_image_mentions_share_ordinal_sequence(
        self, core: ReadFileCore, tmp_path: Path,
    ) -> None:
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "pic.png").write_bytes(_png_bytes())
        fragments = resolve_at_mentions(
            "@notes.txt then @pic.png", core, tmp_path,
            active_model=_vision_model(), image_pipeline_config=_pipeline_config())
        assert fragments is not None
        assert len(fragments) == 3
        assert "Attachment Id: 1" in fragments[0].text
        assert "Attachment Id: 2" in fragments[1].text
        assert fragments[2].type == "image_url"
