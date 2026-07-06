# © Copyright 2026 Aaron Kimball
"""Library logic behind `klorb init`: copies the packaged, spartan `template-config.json` into
place and points a `klorb` executable symlink at the running process's own launcher script.
Shared by the CLI subcommand (`klorb.cli`) and the "Init local klorb config" command palette
action (`klorb.tui.init_commands`) — see docs/specs/klorb-init.md.

`template-config.json` is distinct from `klorb.process_config`'s
`DEFAULT_CONFIG_RESOURCE_NAME` (`default-config.json`): the latter is read directly as a
config layer on every process start and is never copied anywhere, while this module's
`TEMPLATE_CONFIG_RESOURCE_NAME` is only ever copied to disk by `klorb init`, as a starting
point for hand-editing — see docs/specs/process-and-session-config.md.

This module only computes paths, writes files, and returns progress messages; it never
touches `stdout`/`stderr` or a UI toast itself, so each caller can present the same two steps
however fits its own surface (stderr lines for the CLI, a notification for the TUI).
"""

import importlib.resources
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from klorb.process_config import etc_config_path, user_config_path

InitScope = Literal["system", "user"]

TEMPLATE_CONFIG_RESOURCE_NAME = "template-config.json"
"""Filename of the packaged, spartan starter config within the `klorb.resources` package —
see `template_config_text()`."""

EXECUTABLE_NAME = "klorb"
SYSTEM_BIN_DIR = Path("/usr/bin")
USER_BIN_SUBPATH = Path(".local") / "bin"
"""`~`-relative directory `klorb init --user`'s symlink step creates `klorb` inside — joined
onto `Path.home()` lazily, at call time, by `symlink_target_dir()`, rather than cached as a
module-level constant, so a test (or a real `$HOME` change) takes effect without a module
reload."""


class InitError(Exception):
    """Raised when a `klorb init` step can't complete (permission denied, `--system` run as a
    non-root user, a filesystem error creating a directory/file/symlink). The message is safe
    to show to the user verbatim, on stderr or in a UI toast.
    """


@dataclass
class StepResult:
    """The ordered progress messages produced by one `klorb init` step (writing the config
    file, or creating the executable symlink) that actually changed or inspected disk state.
    """

    messages: list[str]


def default_scope() -> InitScope:
    """`"system"` when running as root (effective uid 0), `"user"` otherwise — the same check
    `run_init()` uses to refuse a `"system"` scope from a non-root process, so the unqualified
    `klorb init` never picks a scope it would then immediately reject.
    """
    return "system" if os.geteuid() == 0 else "user"


def config_target_path(scope: InitScope) -> Path:
    """Where `klorb init`'s config-file step writes for `scope`: `etc_config_path()` (honoring
    `$KLORB_ETC_CONFIG`) for `"system"`, `user_config_path()` (honoring `$KLORB_CONFIG_DIR`)
    for `"user"` — the same resolution `klorb.process_config.load_process_config()` reads
    those two layers from.
    """
    return etc_config_path() if scope == "system" else user_config_path()


def symlink_target_dir(scope: InitScope) -> Path:
    """Directory `klorb init`'s symlink step creates `klorb` inside: `/usr/bin` for
    `"system"`, `~/.local/bin` for `"user"`.
    """
    return SYSTEM_BIN_DIR if scope == "system" else Path.home() / USER_BIN_SUBPATH


def template_config_text() -> str:
    """The packaged, spartan starter config's contents, read from `klorb.resources` via
    `importlib.resources` (see `docs/specs/klorb-init.md` and
    `docs/adrs/ship-reference-klorb-config-as-package-data.md`). Copied verbatim, not parsed
    or validated: `klorb init` just places it on disk for the user to edit, unlike
    `klorb.process_config`'s built-in-defaults layer, which is parsed and merged.
    """
    return (
        importlib.resources.files("klorb.resources")
        .joinpath(TEMPLATE_CONFIG_RESOURCE_NAME)
        .read_text(encoding="utf-8")
    )


def write_config_file(scope: InitScope, *, force: bool) -> StepResult:
    """Copy the packaged starter config to `config_target_path(scope)`, creating its parent
    directory as needed. A no-op (one "already exists" message, no write) if the target file
    already exists and `force` is `False`; overwrites it (with a preceding warning message) if
    `force` is `True`. Raises `InitError` if the parent directory or file can't be created.
    """
    target = config_target_path(scope)
    if target.is_file() and not force:
        return StepResult([f"Config file already exists at {target}; not overwriting (use --force)."])

    messages: list[str] = []
    if target.is_file():
        messages.append(f"Overwriting existing config file at {target} (--force given).")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template_config_text(), encoding="utf-8")
    except OSError as exc:
        raise InitError(f"Failed to write config file at {target}: {exc}") from exc
    messages.append(f"Wrote klorb config file to {target}.")
    return StepResult(messages)


def create_symlink(scope: InitScope, *, force: bool) -> StepResult:
    """Create a `klorb` symlink in `symlink_target_dir(scope)` pointing at the launcher script
    that's running this process (`sys.argv[0]`, resolved to an absolute path), creating the
    target directory first if needed. A no-op (one "already exists" message) if a file or
    symlink is already there and `force` is `False`; removes it (with a preceding warning
    message) and creates a fresh symlink if `force` is `True`. Raises `InitError` if the
    directory can't be created or the symlink can't be written.
    """
    bin_dir = symlink_target_dir(scope)
    link_path = bin_dir / EXECUTABLE_NAME
    launcher_path = Path(sys.argv[0]).resolve()

    messages: list[str] = []
    try:
        if link_path.is_symlink() or link_path.exists():
            if not force:
                return StepResult([f"Symlink already exists at {link_path}; not overwriting (use --force)."])
            messages.append(f"Overwriting existing symlink at {link_path} (--force given).")
            link_path.unlink()
        bin_dir.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(launcher_path)
    except OSError as exc:
        raise InitError(f"Failed to create symlink at {link_path}: {exc}") from exc
    messages.append(f"Created klorb executable symlink at {link_path} -> {launcher_path}.")
    return StepResult(messages)


def run_init(scope: InitScope, *, force: bool) -> list[str]:
    """Run both `klorb init` steps for `scope` — write the starter config file, then create
    the executable symlink — stopping at the first one that raises `InitError` rather than
    attempting the next. Refuses a `"system"` scope outright, before either step, unless
    running as root (effective uid 0).

    Returns the combined, ordered progress messages from every step that ran. Neither step's
    own "already exists; not overwriting" outcome is an error: each is independent and
    idempotent, so an already-initialized machine can safely re-run `klorb init` and see the
    other step still complete.
    """
    if scope == "system" and os.geteuid() != 0:
        raise InitError(f"klorb init --system must be run as root (target: {config_target_path(scope)}).")

    messages = list(write_config_file(scope, force=force).messages)
    messages.extend(create_symlink(scope, force=force).messages)
    return messages
