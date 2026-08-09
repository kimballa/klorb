# © Copyright 2026 Aaron Kimball
"""The `type=bash` hook handler: runs a `HookConfig`'s `shell`/`command` as a one-off
subprocess, sandboxed the same way `BashTool` sandboxes an agent-issued command
(`klorb.sandbox.build_bwrap_argv`) -- `HookInput` json on stdin, a `HookOutput` json parsed
from stdout.
"""

import dataclasses
import logging
import os
import subprocess
import uuid
from pathlib import Path

from pydantic import ValidationError

from klorb.config_macros import MacroExpansionError, expand_macros, resolve_macro_values
from klorb.hooks.config import HookConfig
from klorb.hooks.wire import HookInput, HookOutput
from klorb.paths import KLORB_STATE_DIR
from klorb.sandbox import build_bwrap_argv, bwrap_available, compute_sandbox_dirs, path_dirs_from_env
from klorb.session.config import SessionConfig

logger = logging.getLogger(__name__)

HOOK_ENV_FILE_VAR = "KLORB_HOOK_ENV_FILE"
"""Env var a `bash` hook handler's subprocess can read to find its own env file, so it can
read/customize values without them being visible to the model as tool call args. Points at an
empty placeholder file for now -- wiring real, session-scoped contents is a separate concern,
not yet built."""

_HOOK_ENV_FILES_DIR = KLORB_STATE_DIR / "hooks"
"""Where a `bash` hook handler's `KLORB_HOOK_ENV_FILE` placeholder is created -- see
`_hook_env_file`."""


def _hook_env_file() -> Path:
    """Create and return a fresh, empty placeholder file for `KLORB_HOOK_ENV_FILE` -- one per
    handler invocation, removed again by `run_bash_handler` once the subprocess exits."""
    _HOOK_ENV_FILES_DIR.mkdir(parents=True, exist_ok=True)
    path = _HOOK_ENV_FILES_DIR / f"{uuid.uuid4().hex}.env"
    path.touch()
    logger.debug("Created placeholder hook env file %s", path)
    return path


def _build_env(session_config: SessionConfig, hook_env_file: Path) -> dict[str, str]:
    """The environment a `bash` hook handler's subprocess runs with: `HOME`/`USER` shared from
    the klorb process's own environment, `WORKSPACE_ROOT` set to the resolved workspace root,
    followed by `session_config.share_env`/`set_env`'s passthrough (same precedence as
    `klorb.tools.bash.build_bash_env`), plus `KLORB_HOOK_ENV_FILE`."""
    env: dict[str, str] = {}
    for name in ("HOME", "USER"):
        if name in os.environ:
            env[name] = os.environ[name]
    env["WORKSPACE_ROOT"] = str(session_config.workspace.path.resolve(strict=False))
    for name in session_config.share_env:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(session_config.set_env)
    env[HOOK_ENV_FILE_VAR] = str(hook_env_file)
    return env


def _handler_argv(handler: HookConfig, bash_command: str, workspace_root: Path) -> list[str] | None:
    """The inner argv `handler` runs as (before any `bwrap` wrapping), or `None` if neither
    `shell` nor `command` is set. `${home}`/`${workspaceRoot}` are expanded into `command`
    elements, never into `shell`. Raises `MacroExpansionError` for a `command` element with a
    malformed/unrecognized macro reference."""
    if handler.shell is not None:
        return [bash_command, "-c", handler.shell]
    if handler.command is not None:
        macros = resolve_macro_values(workspace_root)
        return [expand_macros(arg, macros=macros) for arg in handler.command]
    return None


def run_bash_handler(
    handler: HookConfig, hook_input: HookInput, *,
    session_config: SessionConfig, bash_command: str, timeout_seconds: float,
) -> HookOutput | None:
    """Run `handler` (a `type="bash"` `HookConfig`) as a sandboxed one-off subprocess: writes
    `hook_input` to its stdin as json and parses its stdout as a `HookOutput`. Returns `None` --
    logged at `warning` -- if `handler` has neither `shell` nor `command` set, a `command`
    element's macro reference is malformed, the process times out, exits non-zero, or its
    stdout isn't valid `HookOutput` json; never raises.
    """
    workspace_root = session_config.workspace.path.resolve(strict=False)
    try:
        argv = _handler_argv(handler, bash_command, workspace_root)
    except MacroExpansionError as exc:
        logger.warning(
            "Hook handler %r for %r: macro expansion failed: %s", handler.name, hook_input.hook, exc)
        return None
    if argv is None:
        logger.warning(
            "Hook handler %r for %r has neither shell nor command set; skipping.",
            handler.name, hook_input.hook)
        return None

    hook_env_file = _hook_env_file()
    env = _build_env(session_config, hook_env_file)
    home = Path(env.get("HOME", str(Path.home())))
    sandboxed = bwrap_available()
    if sandboxed:
        dirs = compute_sandbox_dirs(
            workspace_root=workspace_root, home=home, trusted=session_config.workspace.trusted,
            read_dirs=session_config.read_dirs, write_dirs=session_config.write_dirs,
            read_files=session_config.read_files, write_files=session_config.write_files,
            approved_scopes=session_config.approved_scopes)
        dirs = dataclasses.replace(dirs, write_files=(*dirs.write_files, hook_env_file))
        full_argv = build_bwrap_argv(
            workspace_root=workspace_root, home=home, env=env, dirs=dirs,
            path_dirs=path_dirs_from_env()) + argv
    else:
        logger.debug(
            "bwrap unavailable; running hook handler %r for %r unsandboxed.",
            handler.name, hook_input.hook)
        full_argv = argv

    logger.debug(
        "Spawning bash hook handler %r for %r (sandboxed=%s): %s",
        handler.name, hook_input.hook, sandboxed, argv)
    try:
        completed = subprocess.run(
            full_argv, input=hook_input.model_dump_json(by_alias=True), capture_output=True,
            text=True, timeout=timeout_seconds, cwd=workspace_root, env=env)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Hook handler %r for %r timed out after %.1fs; skipping.",
            handler.name, hook_input.hook, timeout_seconds)
        return None
    except OSError as exc:
        logger.warning(
            "Hook handler %r for %r failed to launch: %s; skipping.",
            handler.name, hook_input.hook, exc)
        return None
    finally:
        hook_env_file.unlink(missing_ok=True)

    if completed.returncode != 0:
        logger.warning(
            "Hook handler %r for %r exited %d; skipping. stderr=%r",
            handler.name, hook_input.hook, completed.returncode, completed.stderr[:500])
        return None
    try:
        return HookOutput.model_validate_json(completed.stdout)
    except ValidationError as exc:
        logger.warning(
            "Hook handler %r for %r produced invalid HookOutput json: %s; skipping.",
            handler.name, hook_input.hook, exc)
        return None
