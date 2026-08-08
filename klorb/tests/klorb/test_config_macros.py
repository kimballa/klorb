# © Copyright 2026 Aaron Kimball
"""Tests for klorb.config_macros."""

from pathlib import Path

import pytest

from klorb.config_macros import (
    HOME_MACRO_NAME,
    WORKSPACE_ROOT_MACRO_NAME,
    MacroExpansionError,
    expand_macros,
    resolve_macro_values,
)


def test_expand_macros_with_no_references_returns_text_unchanged() -> None:
    assert expand_macros("/plain/path", macros={"home": "/home/x"}) == "/plain/path"


def test_expand_macros_substitutes_a_single_macro() -> None:
    assert expand_macros("${home}/scratch", macros={"home": "/home/x"}) == "/home/x/scratch"


def test_expand_macros_substitutes_multiple_macros_in_one_string() -> None:
    result = expand_macros(
        "${home}/${project}/file", macros={"home": "/home/x", "project": "proj"})
    assert result == "/home/x/proj/file"


def test_expand_macros_leaves_a_substituted_value_unscanned() -> None:
    """The defining property of a single left-to-right pass: `${a}`'s value contains the
    literal text `${b}`, but that text must not be expanded a second time -- proves this isn't
    implemented as sequential `str.replace()` calls, which would re-scan already-substituted
    output."""
    result = expand_macros("${a}", macros={"a": "${b}", "b": "OOPS"})
    assert result == "${b}"


def test_expand_macros_unknown_name_raises() -> None:
    with pytest.raises(MacroExpansionError) as exc_info:
        expand_macros("${nope}/x", macros={"home": "/home/x"})
    assert exc_info.value.snippet == "${nope}"


def test_expand_macros_unterminated_reference_raises() -> None:
    with pytest.raises(MacroExpansionError) as exc_info:
        expand_macros("prefix/${home", macros={"home": "/home/x"})
    assert exc_info.value.snippet == "${home"


def test_expand_macros_empty_name_raises() -> None:
    with pytest.raises(MacroExpansionError):
        expand_macros("${}/x", macros={"home": "/home/x"})


def test_expand_macros_forbid_char_rejects_matching_macro_value() -> None:
    with pytest.raises(MacroExpansionError) as exc_info:
        expand_macros("${home}/*.pem", macros={"home": "/ev*il"}, forbid_char="*")
    assert exc_info.value.snippet == "${home}"


def test_expand_macros_forbid_char_allows_non_matching_macro_value() -> None:
    result = expand_macros("${home}/*.pem", macros={"home": "/home/x"}, forbid_char="*")
    assert result == "/home/x/*.pem"


def test_resolve_macro_values_home_is_path_home() -> None:
    macros = resolve_macro_values(Path("/some/workspace"))
    assert macros[HOME_MACRO_NAME] == str(Path.home())


def test_resolve_macro_values_workspace_root_is_resolved(tmp_path: Path) -> None:
    macros = resolve_macro_values(tmp_path)
    assert macros[WORKSPACE_ROOT_MACRO_NAME] == str(tmp_path.resolve(strict=False))
