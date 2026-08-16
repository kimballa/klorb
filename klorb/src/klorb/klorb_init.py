# © Copyright 2026 Aaron Kimball
"""Library logic behind `klorb init`. Shared by the CLI subcommand (`klorb.cli`) and the "Init
local klorb config" command palette action (`klorb.tui.commands.init_commands`) — see
docs/specs/klorb-init.md.

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
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from klorb.paths import get_klorb_data_dir
from klorb.process_config import etc_config_path, user_config_path
from klorb.search_index.embedding import install_embedding_model
from klorb.token_estimate import install_tiktoken_cache

RESOURCE_DATA_RESOURCE_NAME = "data"
"""Directory name of the packaged, installable resource-data tree within the `klorb.resources`
package (`klorb.resources/data/`). Copied flat into `$KLORB_DATA_DIR` itself, not into a
same-named subdirectory."""

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


def is_user_scope_initialized() -> bool:
    """Whether both of `klorb init --user`'s targets already exist on disk: the config file
    at `config_target_path("user")` and the `klorb` symlink at
    `symlink_target_dir("user") / EXECUTABLE_NAME`. When this is `True`,
    `run_init("user", force=False)` is a pure no-op (two "already exists" messages, no
    writes), so the TUI's "Init local klorb config" palette command hides itself rather
    test (`is_symlink() or exists()`), so a broken symlink still counts as already there,
    matching `create_symlink`'s own "already exists; not overwriting" short-circuit.
    """
    config_ok = config_target_path("user").is_file()
    link_path = symlink_target_dir("user") / EXECUTABLE_NAME
    symlink_ok = link_path.is_symlink() or link_path.exists()
    return config_ok and symlink_ok


def template_config_text() -> str:
    """The packaged, spartan starter config's contents, read from `klorb.resources` via
    `importlib.resources` (see `docs/specs/klorb-init.md` and
    `docs/adrs/00053-ship-reference-klorb-config-as-package-data.md`). Copied verbatim, not parsed
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


def copy_tiktoken_cache() -> StepResult:
    """Copy the packaged tiktoken cache tree into `klorb.token_estimate.
    tiktoken_cache_target_dir()` (`$KLORB_DATA_DIR/tiktoken-cache`), creating it as needed, so
    a later klorb process start can point tiktoken at it without needing network access — see
    `klorb.token_estimate.configure_tiktoken_cache_env()`, called from `klorb.cli.main()`.
    Unlike `write_config_file`/`create_symlink`, not scoped to `"system"`/`"user"` — `$KLORB_
    DATA_DIR` has no `"system"`-scope counterpart — so `run_init()` runs this step once
    regardless of `scope`. Raises `InitError` if the target can't be created or written.
    """
    try:
        messages = install_tiktoken_cache()
    except OSError as exc:
        raise InitError(f"Failed to copy tiktoken cache: {exc}") from exc
    return StepResult(messages)


def copy_embedding_model() -> StepResult:
    """Copy the packaged local-search-index embedding model into `klorb.search_index.embedding.
    embedding_model_target_dir()` (`$KLORB_DATA_DIR/embedding-model`), the same
    always-copy/no-`force`-gate treatment `copy_tiktoken_cache()` gets and for the same reason —
    see docs/specs/local-search-index.md. Raises `InitError` if the target can't be created or
    written."""
    try:
        messages = install_embedding_model()
    except OSError as exc:
        raise InitError(f"Failed to copy embedding model: {exc}") from exc
    return StepResult(messages)


def copy_resource_data() -> StepResult:
    """Recursively copies the packaged `klorb.resources/data/` resource tree into
    `get_klorb_data_dir()` itself, creating it as needed. No `force` gate, unlike
    `write_config_file`/`create_symlink` — this is package data, not something a user
    hand-edits, so it's safe to always re-copy. Raises `InitError` if the target can't be
    created or written.
    """
    target = get_klorb_data_dir()
    try:
        with importlib.resources.as_file(
            importlib.resources.files("klorb.resources").joinpath(RESOURCE_DATA_RESOURCE_NAME)
        ) as source:
            shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError as exc:
        raise InitError(f"Failed to copy resource data to {target}: {exc}") from exc
    return StepResult([f"Copied resource data to {target}."])


def run_init(scope: InitScope, *, force: bool) -> list[str]:
    """Run every `klorb init` step for `scope` — write the starter config file, create the
    executable symlink, copy the packaged tiktoken cache tree, copy the packaged embedding model,
    then copy the packaged resource data tree — stopping at the first one that raises `InitError`
    rather than attempting the next. Refuses a `"system"` scope outright, before any step, unless
    running as root (effective uid 0).

    Returns the combined, ordered progress messages from every step that ran. Neither
    scoped step's own "already exists; not overwriting" outcome is an error: each is
    independent and idempotent, so an already-initialized machine can safely re-run `klorb
    init` and see the other steps still complete.
    """
    if scope == "system" and os.geteuid() != 0:
        raise InitError(
            f"klorb init --system must be run as root (target: {config_target_path(scope)}).")

    messages = list(write_config_file(scope, force=force).messages)
    messages.extend(create_symlink(scope, force=force).messages)
    messages.extend(copy_tiktoken_cache().messages)
    messages.extend(copy_embedding_model().messages)
    messages.extend(copy_resource_data().messages)
    return messages
