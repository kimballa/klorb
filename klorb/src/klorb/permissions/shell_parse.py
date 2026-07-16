# © Copyright 2026 Aaron Kimball
"""Parses a bash command string into the shape `klorb.permissions.command_access`'s
`CommandPermissionsTable` (and `klorb.permissions.workspace`'s `evaluate_write`/
`resolve_and_evaluate_read`) can evaluate: every simple command's own argv, every redirection's
file target, and every construct the walker can't confidently classify — escalated to "ask",
never silently to "allow". See docs/specs/bash-tool-and-command-permissions.md.

Parsing itself is delegated entirely to `shfmt --to-json` (the `shfmt-py` pypi package wrapping
`mvdan/sh`'s `syntax` package) — never a regexp/lexical classifier; see
docs/adrs/shell-out-to-shfmt-for-bash-parsing.md for why. This module has no dependency on
`klorb.tools`/`klorb.session`:
it returns plain data (`BashCommandAnalysis`) for `klorb.tools.bash.BashTool` to combine with
`CommandPermissionsTable`/`evaluate_write`/`resolve_and_evaluate_read`, mirroring the
`directory_access`/`workspace` split.
"""

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SAFE_STDIN_CONSUMERS = frozenset({
    "cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "sort", "uniq", "wc", "jq",
    "git",
})
"""Commands that only ever consume stdin as inert data — read, search, filter, or summarize it,
never execute it — so they may receive heredoc/herestring/piped content under ordinary
`CommandRules` evaluation. Any other command on the receiving end of a pipe, heredoc, or
herestring is escalated to "ask" regardless of what an allow-rule for its own argv0 would
otherwise say — see `_check_stdin_consumer`.

Deliberately excludes `tee` (writes stdin to a file — that write target would need its own
`writeDirs` check, which this consumer-safety check doesn't perform) and `xargs` (constructs and
runs commands from its input, which is exactly the class of risk this check exists to catch)."""

HIDDEN_EFFECT_COMMANDS = frozenset({"eval", "exec", "source", "."})
"""Commands whose real effect isn't visible in their own argv — always escalated to "ask",
regardless of `CommandRules`."""

_PIPE_OPS = frozenset({13, 14})
"""`BinaryCmd.Op` values for `|` (Pipe) and `|&` (PipeAll) — verified empirically against this
project's pinned `shfmt` build (see `klorb.permissions.command_access` module docstring for why
these are read from real `shfmt --to-json` output, not assumed from documentation): the `Y` side
of one of these receives the `X` side's stdout, so it's subject to the same
safe-stdin-consumer check as a heredoc/herestring target — see `_check_stdin_consumer`."""

_WRITE_REDIR_OPS = frozenset({63, 64, 66, 68, 69, 74, 76})
"""`Redirect.Op` values whose `Word` names a filesystem target opened for writing: `>` (63),
`>>` (64), `<>` (66), `>&`/dup (68 — see `_looks_like_fd_dup`, since the same op code is used for
both `N>&file` and the numeric-fd-duplication form `N>&M`), `>|` (69), `&>` (74), `&>>` (76).
Routed through `klorb.permissions.workspace.evaluate_write` by `klorb.tools.bash.BashTool` —
a bash redirection is a filesystem write, governed by the same `DirectoryAccessTable`/
`writeDirs` a model-invoked `EditFile` call already is."""

_READ_REDIR_OPS = frozenset({65})
"""`Redirect.Op` for `<` (RdrIn) — the only redirection operator that opens its `Word` target
for reading rather than writing; routed through `resolve_and_evaluate_read` instead of
`evaluate_write`."""

_INLINE_CONTENT_REDIR_OPS = frozenset({71, 72, 73})
"""`Redirect.Op` values for `<<` (Hdoc), `<<-` (DashHdoc), and `<<<` (WordHdoc/herestring): the
redirected content is inline in the script itself, not a filesystem path, so there is no
`evaluate_write`/`resolve_and_evaluate_read` candidate here at all — instead, the *owning*
`Stmt`'s own command is checked against `SAFE_STDIN_CONSUMERS` (see `_walk_stmt`)."""

RedirDirection = Literal["read", "write"]


@dataclass(frozen=True)
class RedirectTarget:
    """One redirection's file target extracted from the AST, and which permissions direction
    (`klorb.permissions.workspace.evaluate_write` for `"write"`, `resolve_and_evaluate_read` for
    `"read"`) `BashTool` should check it against.

    `source_text` is the exact original source of the *owning statement* (the simple command
    this redirect belongs to, including the redirect itself, e.g. `"cat file > out.txt"`) —
    sliced directly from the raw command string via AST offsets, never reconstructed from parsed
    parts, so original quoting/spacing survives. It's purely a display aid (see
    `klorb.permissions.table.PermissionAskItem.item_command_text`), never itself the resource a
    grant is checked or persisted against, unlike `target`/`direction`."""

    target: str
    direction: RedirDirection
    source_text: str


@dataclass(frozen=True)
class SimpleCommand:
    """One parsed simple command: `argv` (argv0 first) is what `CommandPermissionsTable` matches
    against; `source_text` is the exact original source of the `CallExpr`/`DeclClause` node it
    came from — sliced directly from the raw command string via AST offsets, not reconstructed
    from `argv` (which would lose original quoting/spacing, e.g. `git commit -m "a message"`
    losing its quotes if rejoined with spaces). Purely a display aid (see
    `klorb.permissions.table.PermissionAskItem.item_command_text`), never itself the resource a
    grant is checked or persisted against, unlike `argv`."""

    argv: list[str]
    source_text: str


@dataclass(frozen=True)
class ForcedAskReason:
    """One reason the walker itself escalated to "ask", paired with the exact original source
    text of whichever AST node the reason is actually about (a `CallExpr`/`DeclClause` for a
    non-literal-argument or hidden-effect command, the owning `Stmt` for a backgrounded command,
    an unsafe stdin consumer, or a redirect-level issue — see each `forced_ask_reasons.append`
    call site) — sliced directly from the raw command string via AST offsets. Without this, a
    structural item had nothing but the abstract `reason` text to show a UI
    (`klorb.tui.panels.permission_ask_panel.PermissionAskPanel`): two structural items from the same
    compound command (e.g. `echo $SHELL; echo $HOME`, both non-literal-argument reasons) were
    otherwise indistinguishable from each other in the UI, both showing the identical generic
    reason string with no indication of which specific command each one is actually about."""

    reason: str
    source_text: str


@dataclass
class BashCommandAnalysis:
    """Everything `klorb.tools.bash.BashTool` needs to evaluate one parsed command: every simple
    command's own argv (argv0 first, from every pipeline stage, `&&`/`||`/`;` list member,
    subshell, command substitution, control-flow body, etc. — see `parse_command`), every
    redirection's file target, and every reason the walker itself escalated to "ask" (a
    backgrounded top-level `&`, a heredoc/herestring/pipe feeding an unsafe consumer, a
    non-literal argv token, an unrecognized AST node, or a hidden-effect command like `eval`).

    `BashTool.apply()` combines `simple_commands`/`redirects` with `CommandPermissionsTable`'s
    own verdicts and `forced_ask_reasons` via the same strictest-wins rule
    `klorb.permissions.workspace.evaluate_write` already uses for read/write — see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md.

    `command_count` tallies every executable node the walker visits — every `CallExpr` with a
    non-empty `Args` (`_handle_call_expr`), every `DeclClause` (`_handle_decl_clause`), and every
    unrecognized construct `_walk_cmd` falls through on — regardless of whether that node's own
    argv turned out fully literal (landing in `simple_commands`) or not (landing in
    `forced_ask_reasons` instead, via the early-return branches in those same two handlers, still
    incrementing this count first). This is deliberately a separate tally from
    `len(simple_commands)`: a `for`/`if`/`case` body command with a non-literal argument (`cat
    "$f"`) never reaches `simple_commands` at all, but it's still exactly one more real command
    that will execute — undercounting it would make `BashTool._classify`'s `is_compound` signal
    (see its own docstring) miss a command line that's genuinely made of more than one action just
    because every action in it happened to need a non-literal-argument ask rather than a
    `CommandRules` one. Bare variable-assignment statements (`FOO=bar`, no `Args` at all) invoke
    nothing, so they're never counted; a `TestClause`/`ArithmCmd` guard (`[[ ... ]]`/`(( ... ))`)
    likewise invokes no external program of its own and is never counted either — only a real
    `[`/`test` invocation (an ordinary `CallExpr`) is.

    `raw_command` is the original command string `parse_command` was given, set once at
    construction and never mutated afterward — every `source_text` field above (`SimpleCommand`/
    `ForcedAskReason`/`RedirectTarget`'s own) is a slice of this same string, computed on demand
    by `_node_text()` from a node's AST `Pos`/`End` byte offsets rather than threaded through
    every walker function as a separate parameter.
    """

    simple_commands: list[SimpleCommand] = field(default_factory=list)
    redirects: list[RedirectTarget] = field(default_factory=list)
    forced_ask_reasons: list[ForcedAskReason] = field(default_factory=list)
    command_count: int = 0
    raw_command: str = ""


class ShellParseError(Exception):
    """Raised when `shfmt --to-json` can't be run at all, or exits non-zero — either because
    `shfmt` itself couldn't handle the input, or because the model produced malformed shell
    syntax; one exception type covers both, since the caller's response (surface it to the
    model as an ordinary tool error so it can retry with simpler syntax) is the same either way.
    Never routed through the permissions system: a syntax error isn't a permission verdict.
    Every raise site also logs at `error` level (see `parse_command`), so recurring parse
    failures are visible in klorb's own logs even though the model just sees a retryable error.
    """


def _resolve_shfmt_command(shfmt_command: str) -> str:
    """Resolve a configured `shfmt_command` (default `"shfmt"`, `tools.bash.shfmtCommand`) to an
    actually-runnable path. A value that already looks like a path (contains a path separator)
    is returned unchanged — the caller explicitly chose it. Otherwise, tries the directory
    containing the running Python interpreter (`sys.executable`) first — where `shfmt-py` (a
    "scripts"-only wheel with no importable Python API of its own) installs the exact-pinned
    `shfmt` binary for a venv install (see `pyproject.toml`'s exact-version pin and
    docs/adrs/shell-out-to-shfmt-for-bash-parsing.md), alongside `python`/`pip` themselves,
    whether or not that venv's `bin/` happens to be on `PATH` for the current process. Only if no
    such sibling binary exists does this fall back to `PATH`. This order is deliberate, not
    incidental: a machine with its own system-wide `shfmt` on `PATH` (a different, unpinned
    version) must not shadow the exact build this module's `Redirect.Op` tables (`_WRITE_REDIR_
    OPS` etc.) were empirically verified against — a version mismatch there doesn't error, it
    silently degrades to "ask" more often as unrecognized operator codes fail closed (see the ADR
    above), which is easy to mistake for a permissions bug rather than a `shfmt` version mismatch.
    """
    if "/" in shfmt_command or "\\" in shfmt_command:
        return shfmt_command
    candidate = Path(sys.executable).parent / shfmt_command
    if candidate.is_file():
        return str(candidate)
    if shutil.which(shfmt_command) is not None:
        return shfmt_command
    return shfmt_command


def parse_command(command: str, shfmt_command: str) -> BashCommandAnalysis:
    """Parse `command` via `{shfmt_command} --to-json` and walk the resulting AST into a
    `BashCommandAnalysis`. `shfmt_command` is resolved via `_resolve_shfmt_command()` first, so
    a bare default (`"shfmt"`) still works when invoked from an unactivated venv. Raises
    `ShellParseError` if `shfmt_command` can't be run at all, times out, or exits non-zero (a
    real shell syntax error in `command`)."""
    shfmt_command = _resolve_shfmt_command(shfmt_command)
    try:
        result = subprocess.run(
            [shfmt_command, "--to-json"], input=command, capture_output=True, text=True, timeout=10)
    except FileNotFoundError as exc:
        logger.error("%r not found; cannot parse shell commands", shfmt_command)
        raise ShellParseError(f"{shfmt_command!r} not found; cannot parse shell commands") from exc
    except subprocess.TimeoutExpired as exc:
        logger.error("%s --to-json timed out parsing command: %r", shfmt_command, command)
        raise ShellParseError(f"{shfmt_command} --to-json timed out parsing the command") from exc

    if result.returncode != 0:
        logger.error(
            "Failed to parse command %r: %s", command, result.stderr.strip())
        raise ShellParseError(f"Failed to parse command: {result.stderr.strip()}")

    try:
        ast = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("%s --to-json produced unparseable output for %r: %s", shfmt_command, command, exc)
        raise ShellParseError(f"{shfmt_command} --to-json produced unparseable output: {exc}") from exc

    analysis = BashCommandAnalysis(raw_command=command)
    _walk_stmts(ast.get("Stmts", []), analysis)
    return analysis


def _node_text(node: dict[str, Any], analysis: BashCommandAnalysis) -> str:
    """Exact original source text of `node` (any `Stmt`/`Cmd`-shaped dict — every such node
    `shfmt --to-json` produces carries its own `Pos`/`End`, each an object with an integer byte
    `Offset`), sliced directly from `analysis.raw_command` rather than reconstructed from parsed
    parts — preserves original quoting/spacing a `' '.join(argv)`-style reconstruction would
    lose. See `SimpleCommand`/`ForcedAskReason`/`RedirectTarget`'s own `source_text` fields."""
    return analysis.raw_command[node["Pos"]["Offset"]:node["End"]["Offset"]]


def _word_literal(word: dict[str, Any] | None) -> str | None:
    """Return `word`'s literal text if every one of its `Parts` is a plain `Lit`, a `SglQuoted`
    (single quotes never interpolate — its text is always literal), or a `DblQuoted` whose own
    nested `Parts` are themselves all literal by this same rule (concatenated) — or `None` if
    `word` is `None`, isn't a plain Word at all (e.g. an `ArrayExpr`, which has no `Parts` key),
    or contains any part that isn't one of those three (`ParamExp`/`CmdSubst`/`ArithmExp`/etc.)
    — the class of token GuardFall documents as the common bypass vector. Quoting alone
    (`"hello world"`, `'hello world'`) is not itself a bypass
    signal — treating every quoted argument as non-literal would make ordinary, safe commands
    (`git commit -m "a message"`) escalate to "ask" on every call, which is not what "fail
    closed on anything not confidently classified" is protecting against.
    """
    if word is None or "Parts" not in word:
        return None
    return _concat_literal_parts(word["Parts"])


def _concat_literal_parts(parts: list[dict[str, Any]]) -> str | None:
    values: list[str] = []
    for part in parts:
        text = _literal_part_text(part)
        if text is None:
            return None
        values.append(text)
    return "".join(values)


def _literal_part_text(part: dict[str, Any]) -> str | None:
    part_type = part.get("Type")
    if part_type == "Lit":
        return str(part["Value"])
    if part_type == "SglQuoted":
        return str(part.get("Value", ""))
    if part_type == "DblQuoted":
        return _concat_literal_parts(part.get("Parts", []))
    return None


def _looks_like_fd_dup(literal: str) -> bool:
    """True if a `_WRITE_REDIR_OPS` redirect's literal target is a bare file descriptor number
    (`2>&1`) or `-` (close the fd) rather than an actual filesystem path — `shfmt` uses the same
    `Op` code for both `N>&file` and `N>&M`, so the target text is the only way to tell them
    apart. A bare fd number/`-` has no filesystem candidate to evaluate at all."""
    return literal == "-" or literal.isdigit()


def _scan_for_cmdsubst(node: Any, analysis: BashCommandAnalysis) -> None:
    """Recursively scan `node` (a JSON-decoded dict/list of arbitrary shape) for any embedded
    `CmdSubst` node, walking its `Stmts` as nested commands wherever found. This is the single
    defense-in-depth mechanism used everywhere a `Word` (or an opaque expression tree this
    walker doesn't otherwise model — `TestClause`/`ArithmCmd`/a `CStyleLoop`'s `Init`/`Cond`/
    `Post`) might embed `$(...)`/backtick command substitution, so a dangerous command hidden
    inside e.g. a case pattern, a `for`-loop word list, or an arithmetic expression is still
    extracted and classified, even though the *outer* construct isn't itself treated as a
    literal candidate command.
    """
    if isinstance(node, dict):
        if node.get("Type") == "CmdSubst":
            _walk_stmts(node.get("Stmts", []), analysis)
            return
        for value in node.values():
            _scan_for_cmdsubst(value, analysis)
    elif isinstance(node, list):
        for item in node:
            _scan_for_cmdsubst(item, analysis)


def _check_stdin_consumer(
    cmd: dict[str, Any] | None, scope: dict[str, Any], analysis: BashCommandAnalysis, *, source: str,
) -> None:
    """Escalate to "ask" unless `cmd` is a `CallExpr` whose literal argv0 is in
    `SAFE_STDIN_CONSUMERS` — called for the receiving side of a pipe (`_handle_binary_cmd`, where
    `scope` is that side's own `Stmt`) and for the owning command of a heredoc/herestring
    (`_walk_stmt`, where `scope` is the owning `Stmt` itself). `scope` supplies the
    `ForcedAskReason.source_text` for display purposes — `cmd` alone can be `None` (a non-
    `CallExpr` receiving command) or narrower than the whole statement, so `scope` is always the
    `Stmt` whether or not `cmd` itself is usable."""
    argv0 = None
    if cmd is not None and cmd.get("Type") == "CallExpr":
        args = cmd.get("Args", [])
        if args:
            argv0 = _word_literal(args[0])
    if argv0 not in SAFE_STDIN_CONSUMERS:
        label = repr(argv0) if argv0 is not None else "a non-literal/complex command"
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason=(
                f"{source} feeds stdin content into {label}, which is not in the safe "
                f"stdin-consumer allowlist {sorted(SAFE_STDIN_CONSUMERS)}"),
            source_text=_node_text(scope, analysis)))


IMPLICIT_READ_COMMANDS = frozenset({"cat", "less", "more", "grep", "head", "tail",
    "sort", "uniq", "wc", "jq", "ls", "cd", "pushd"})
"""Commands whose non-flag literal arguments are file paths to *read* (displayed, sorted,
counted, or queried, but never modified) — so a bare invocation of one of these (not fed into
anything else, and not itself redirected) checks each such argument against `readDirs` in
addition to `CommandRules`, the same protection a real `ReadFile` call already gets. See
`_maybe_add_implicit_reads`."""


def _walk_stmts(stmts: list[dict[str, Any]], analysis: BashCommandAnalysis) -> None:
    for stmt in stmts:
        _walk_stmt(stmt, analysis)


def _walk_stmt(
    stmt: dict[str, Any], analysis: BashCommandAnalysis, *, is_piped_into: bool = False,
) -> None:
    """Walk one `Stmt`-shaped node — this shape recurs identically for `File.Stmts` elements,
    `Subshell`/`Block`/`CmdSubst`'s own `Stmts`, `IfClause`/`WhileClause`/`ForClause`'s `Cond`/
    `Then`/`Else`/`Do` elements, `CaseClause` item `Stmts`, and a `BinaryCmd`'s own `X`/`Y` sides
    (verified empirically: `X`/`Y` each carry their own `Redirs`/`Background`, exactly like a
    top-level statement) — so this one function is the sole recursion point for all of them.
    `is_piped_into` is set by `_handle_binary_cmd` only for the receiving (`Y`) side of a pipe —
    see `_maybe_add_implicit_reads`.
    """
    if stmt.get("Background"):
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason="command is backgrounded with a top-level '&', which is rejected at parse time",
            source_text=_node_text(stmt, analysis)))

    cmd = stmt.get("Cmd")
    redirs = stmt.get("Redirs", [])
    for redir in redirs:
        _walk_redir(redir, stmt, analysis)
        if redir.get("Op") in _INLINE_CONTENT_REDIR_OPS:
            _check_stdin_consumer(cmd, stmt, analysis, source="a heredoc/herestring")

    if cmd is not None:
        _walk_cmd(cmd, analysis)
        if not is_piped_into and not redirs:
            _maybe_add_implicit_reads(cmd, analysis)


def _maybe_add_implicit_reads(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """A bare `cat`/`less` invocation (not piped into from elsewhere, not itself redirected —
    both already ruled out by `_walk_stmt`'s caller) is functionally a file read: extract each
    non-flag literal argument as an additional `RedirectTarget(direction="read")`, so it's
    checked against `readDirs` the same way a real `ReadFile` call would be — on top of, not
    instead of, the ordinary `CommandRules` check on the `cat`/`less` invocation itself.
    """
    if cmd.get("Type") != "CallExpr":
        return
    args = cmd.get("Args", [])
    if not args or _word_literal(args[0]) not in IMPLICIT_READ_COMMANDS:
        return
    source_text = _node_text(cmd, analysis)
    for arg_word in args[1:]:
        literal = _word_literal(arg_word)
        if literal is not None and not literal.startswith("-"):
            analysis.redirects.append(RedirectTarget(
                target=literal, direction="read", source_text=source_text))


def _walk_redir(redir: dict[str, Any], stmt: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """`stmt` is the owning statement (the simple command this redirect belongs to) — used only
    for `ForcedAskReason`/`RedirectTarget.source_text`, since a redirect target alone (`>out.txt`)
    is meaningless to a user without the command it's attached to."""
    word = redir.get("Word")
    if word is not None:
        _scan_for_cmdsubst(word, analysis)
    hdoc = redir.get("Hdoc")
    if hdoc is not None:
        _scan_for_cmdsubst(hdoc, analysis)

    op = redir.get("Op")
    if op in _INLINE_CONTENT_REDIR_OPS:
        return  # no filesystem candidate; safety is the owning Stmt's stdin-consumer check.

    literal = _word_literal(word)
    if literal is None:
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason="redirection target is not a literal path (variable/command substitution)",
            source_text=_node_text(stmt, analysis)))
        return

    if op in _WRITE_REDIR_OPS:
        if _looks_like_fd_dup(literal):
            return  # fd duplication (e.g. `2>&1`), not a filesystem path.
        analysis.redirects.append(RedirectTarget(
            target=literal, direction="write", source_text=_node_text(stmt, analysis)))
    elif op in _READ_REDIR_OPS:
        analysis.redirects.append(RedirectTarget(
            target=literal, direction="read", source_text=_node_text(stmt, analysis)))
    else:
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason=f"unrecognized redirection operator {op!r}",
            source_text=_node_text(stmt, analysis)))


def _handle_call_expr(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    for assign in cmd.get("Assigns", []):
        value = assign.get("Value")
        if value is not None:
            _scan_for_cmdsubst(value, analysis)

    args = cmd.get("Args", [])
    if not args:
        return  # a bare assignment statement (`FOO=bar`) invokes nothing.
    analysis.command_count += 1
    source_text = _node_text(cmd, analysis)

    argv: list[str] = []
    non_literal = False
    for arg_word in args:
        _scan_for_cmdsubst(arg_word, analysis)
        literal = _word_literal(arg_word)
        if literal is None:
            non_literal = True
        else:
            argv.append(literal)

    if non_literal:
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason="command has a non-literal argument (variable/command substitution/glob expansion)",
            source_text=source_text))
        return

    if argv[0] in HIDDEN_EFFECT_COMMANDS:
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason=f"{argv[0]!r} can hide its real effect from its own argv",
            source_text=source_text))

    analysis.simple_commands.append(SimpleCommand(argv=argv, source_text=source_text))


def _handle_binary_cmd(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    x_side, y_side = cmd.get("X"), cmd.get("Y")
    is_pipe = cmd.get("Op") in _PIPE_OPS
    if is_pipe and y_side is not None:
        _check_stdin_consumer(y_side.get("Cmd"), y_side, analysis, source="a pipe")
    if x_side is not None:
        _walk_stmt(x_side, analysis)
    if y_side is not None:
        _walk_stmt(y_side, analysis, is_piped_into=is_pipe)


def _handle_stmts_container(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """`Subshell`/`Block`: both are just a bare `Stmts` list, executed as its own scope."""
    _walk_stmts(cmd.get("Stmts", []), analysis)


def _walk_if_like(node: dict[str, Any] | None, analysis: BashCommandAnalysis) -> None:
    """`IfClause`'s own shape, and (recursively) its `Else` — an `elif` chain nests as `Else`
    being another `Cond`/`Then`/`Else`-shaped dict with no `Type` of its own; a final bare
    `else` has only `Then`. Verified empirically against `shfmt --to-json` output for an
    `if`/`elif`/`else` chain."""
    if node is None:
        return
    if "Cond" in node:
        _walk_stmts(node["Cond"], analysis)
    _walk_stmts(node.get("Then", []), analysis)
    _walk_if_like(node.get("Else"), analysis)


def _handle_while_clause(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """Covers both `while` and `until` — `shfmt` represents both as `WhileClause`, distinguished
    only by an `Until` bool field that doesn't change this walk."""
    _walk_stmts(cmd.get("Cond", []), analysis)
    _walk_stmts(cmd.get("Do", []), analysis)


def _handle_for_clause(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    loop = cmd.get("Loop") or {}
    if loop.get("Type") == "WordIter":
        for item in loop.get("Items", []):
            _scan_for_cmdsubst(item, analysis)
    else:
        # CStyleLoop (`for ((i=0; i<10; i++))`): its Init/Cond/Post are arithmetic expression
        # trees this walker doesn't otherwise model; scan generically for embedded CmdSubst.
        _scan_for_cmdsubst(loop, analysis)
    _walk_stmts(cmd.get("Do", []), analysis)


def _handle_case_clause(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    word = cmd.get("Word")
    if word is not None:
        _scan_for_cmdsubst(word, analysis)
    for item in cmd.get("Items", []):
        for pattern in item.get("Patterns", []):
            _scan_for_cmdsubst(pattern, analysis)
        _walk_stmts(item.get("Stmts", []), analysis)


def _handle_func_decl(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    body = cmd.get("Body")
    if body is not None:
        _walk_stmt(body, analysis)


def _handle_decl_clause(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """`export`/`declare`/`local`/`readonly`: modeled by `shfmt` as their own `DeclClause` node
    (a `Variant` literal plus `Args`, each an assign-like `{Name, Value}` or `{Naked, Name}`),
    not a plain `CallExpr` — reconstructed here into an argv-shaped candidate (`["export",
    "FOO=bar", "BAZ"]`) so ordinary `CommandRules` patterns can still match it.
    """
    analysis.command_count += 1
    source_text = _node_text(cmd, analysis)
    variant = cmd.get("Variant", {}).get("Value")
    argv: list[str] = [variant] if variant else []
    non_literal = variant is None

    for arg in cmd.get("Args", []):
        name = arg.get("Name", {}).get("Value")
        if arg.get("Naked") or arg.get("Value") is None:
            token = name
        else:
            value_word = arg["Value"]
            _scan_for_cmdsubst(value_word, analysis)
            literal = _word_literal(value_word)
            token = f"{name}={literal}" if literal is not None else None
        if token is None:
            non_literal = True
        else:
            argv.append(token)

    if non_literal or not argv:
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason=f"{cmd.get('Type')} ({variant!r}) has a non-literal argument",
            source_text=source_text))
        return
    analysis.simple_commands.append(SimpleCommand(argv=argv, source_text=source_text))


def _handle_time_clause(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    stmt = cmd.get("Stmt")
    if stmt is not None:
        _walk_stmt(stmt, analysis)


def _handle_transparent_expr(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    """`TestClause` (`[[ ... ]]`) and `ArithmCmd` (`(( ... ))`): safe control-flow constructs
    that don't themselves invoke an arbitrary program — only scanned for embedded `CmdSubst`,
    never forced to "ask" just for appearing."""
    _scan_for_cmdsubst(cmd, analysis)


_CMD_HANDLERS = {
    "CallExpr": _handle_call_expr,
    "BinaryCmd": _handle_binary_cmd,
    "Subshell": _handle_stmts_container,
    "Block": _handle_stmts_container,
    "IfClause": _walk_if_like,
    "WhileClause": _handle_while_clause,
    "ForClause": _handle_for_clause,
    "CaseClause": _handle_case_clause,
    "FuncDecl": _handle_func_decl,
    "DeclClause": _handle_decl_clause,
    "TimeClause": _handle_time_clause,
    "TestClause": _handle_transparent_expr,
    "ArithmCmd": _handle_transparent_expr,
}
"""Dispatch table from a `Cmd` node's `Type` to its walker. Anything absent here (`CoprocClause`,
`LetClause`, or a future `shfmt`/AST-shape change) falls through `_walk_cmd`'s `else` branch:
forced to "ask" and generically scanned for embedded `CmdSubst`, never silently ignored — the
fail-closed rule applies to unrecognized *node types* exactly as it does to non-literal tokens."""


def _walk_cmd(cmd: dict[str, Any], analysis: BashCommandAnalysis) -> None:
    cmd_type = cmd.get("Type")
    handler = _CMD_HANDLERS.get(cmd_type) if isinstance(cmd_type, str) else None
    if handler is None:
        analysis.command_count += 1
        analysis.forced_ask_reasons.append(ForcedAskReason(
            reason=f"unrecognized shell construct: {cmd_type!r}",
            source_text=_node_text(cmd, analysis)))
        _scan_for_cmdsubst(cmd, analysis)
        return
    handler(cmd, analysis)
