# © Copyright 2026 Aaron Kimball
"""`${...}` macro expansion for config-file string values."""

from pathlib import Path

from klorb.paths import get_klorb_data_dir

HOME_MACRO_NAME = "home"
WORKSPACE_ROOT_MACRO_NAME = "workspaceRoot"
KLORB_DATA_DIR_MACRO_NAME = "klorbDataDir"


class MacroExpansionError(Exception):
    """Raised by `expand_macros` when `text` contains a malformed or unrecognized `${...}`
    reference. `snippet` is the exact substring (verbatim from `text`) that triggered the
    error — a caller with access to the raw config-file source can `str.find()` it to recover
    a line/column for the error message, since macro tokens use only ASCII identifier
    characters and never require JSON escaping.
    """

    def __init__(self, message: str, *, snippet: str) -> None:
        super().__init__(message)
        self.message = message
        self.snippet = snippet


def resolve_macro_values(workspace_root: Path) -> dict[str, str]:
    """The current, fixed value of every recognized macro."""
    return {
        HOME_MACRO_NAME: str(Path.home()),
        WORKSPACE_ROOT_MACRO_NAME: str(workspace_root.resolve(strict=False)),
        KLORB_DATA_DIR_MACRO_NAME: str(get_klorb_data_dir()),
    }


def expand_macros(text: str, *, macros: dict[str, str], forbid_char: str | None = None) -> str:
    """Expand every `${name}` reference in `text` against `macros`, in a single left-to-right
    pass over the original string: each `${...}` span is replaced with its macro's value, and
    that value is never itself re-scanned for further `${...}` references, so a macro value
    that happens to contain the literal text `${...}` is not expanded a second time.

    Raises `MacroExpansionError` for an unterminated `${` (no matching `}`), an empty name
    (`${}`), or a name absent from `macros` — a config author's typo must never silently
    produce a rule that quietly fails to match anything (dangerous for a `deny` rule) or a
    path built from an empty substitution (e.g. `${workspaceRoot}` silently becoming `""` would
    turn `${workspaceRoot}/secrets` into the absolute path `/secrets`).

    `forbid_char`, if given, rejects (also via `MacroExpansionError`) expanding a macro whose
    resolved value contains that character — used by callers expanding into a `readFiles`/
    `writeFiles` rule to guarantee expansion never introduces a new `*` wildcard
    metacharacter that the rule's author didn't type themselves; see
    docs/adrs/00175-file-rule-macro-values-may-not-contain-a-literal-star.md.
    """
    pieces: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        start = text.find("${", i)
        if start == -1:
            pieces.append(text[i:])
            break
        pieces.append(text[i:start])
        end = text.find("}", start + 2)
        if end == -1:
            snippet = text[start:start + 40]
            raise MacroExpansionError(f"unterminated macro reference {snippet!r}", snippet=snippet)
        name = text[start + 2:end]
        snippet = text[start:end + 1]
        if not name:
            raise MacroExpansionError("empty macro reference '${}'", snippet=snippet)
        if name not in macros:
            raise MacroExpansionError(f"unrecognized macro {snippet!r}", snippet=snippet)
        value = macros[name]
        if forbid_char is not None and forbid_char in value:
            raise MacroExpansionError(
                f"expanding {snippet!r} would introduce a literal {forbid_char!r} character "
                f"into a file-access rule, which would change its wildcard matching semantics",
                snippet=snippet)
        pieces.append(value)
        i = end + 1
    return "".join(pieces)
