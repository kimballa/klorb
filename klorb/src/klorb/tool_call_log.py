# © Copyright 2026 Aaron Kimball
"""Out-of-band, file-based audit trail of every tool call a session executes.

Distinct from klorb's ordinary `logging`-based instrumentation (see `klorb.logging_config`):
when active, this always appends directly to a fixed file, `tool-calls.log` in the current
working directory, regardless of the active logging configuration, handlers, or level.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TOOL_CALLS_LOG_FILENAME = "tool-calls.log"

LOG_TOOL_CALLS_ENV_VAR = "LOG_TOOL_CALLS"

LOG_TOOL_CALLS_CONFIG_KEY = "tools.logCalls"
"""On-disk `klorb-config.json` key for `ProcessConfig.log_tool_calls` — see
`klorb.process_config.PROCESS_KEY_MAP`."""

_io_error_already_logged = False
"""Whether `log_tool_call()` has already reported an `IOError` via `logger.error()` once this
process — see that function's docstring for why only the first one is ever logged."""


def _env_var_truthy(value: str | None) -> bool:
    """Return whether an env var string counts as "set": `"1"` or `"true"` (case-insensitive),
    exactly the two spellings `LOG_TOOL_CALLS` recognizes — `None`/anything else is not set.
    """
    return value is not None and value.strip().lower() in ("1", "true")


def tool_call_logging_enabled(config_enabled: bool | None) -> bool:
    """Resolve whether out-of-band tool call logging is active for this process.

    `config_enabled` is the value of `ProcessConfig.log_tool_calls`, which is itself
    resolved (by `klorb.cli.main()`) from the `tools.logCalls` config key and the
    `--log-tool-calls`/`--no-log-tool-calls` CLI flag pair. When that value is an
    explicit `True` or `False`, it is authoritative and the `LOG_TOOL_CALLS`
    environment variable is not consulted. Only when it is still `None` (no config
    key and no CLI flag set) does the environment variable get a chance to enable
    logging, so an explicit `--no-log-tool-calls` overrides `LOG_TOOL_CALLS=true`.

    The environment variable is read here, at call time, rather than folded into
    `ProcessConfig` when it's loaded, so it's honored even by a caller that
    constructs a `ProcessConfig`/`Session` directly without going through
    `klorb.cli.main()` (in which case `config_enabled` will be `None`).
    """
    if config_enabled is not None:
        return config_enabled
    return _env_var_truthy(os.environ.get(LOG_TOOL_CALLS_ENV_VAR))


def log_tool_call(name: str, args: dict[str, Any], result: Any, error: str | None) -> None:
    """Append one entry recording a finished tool call to `tool-calls.log` in the current
    working directory, creating the file if it doesn't already exist.

    Each entry is separated from the file's existing contents (if any) by a blank line,
    followed by a `---` divider line, an ISO-8601 timestamp line, `"Request:"` and the call's
    name/arguments as pretty-printed JSON, and `"Response:"` and the call's result (or `error`,
    on failure — the same success/failure discriminant as `klorb.session.ToolCallEvent`) as
    pretty-printed JSON.

    Never raises: an `IOError` (e.g. an unwritable working directory, a full disk) is caught and
    reported via `logger.error()` instead, since this is a best-effort audit trail, not a
    behavior a tool call's success should depend on. Only the first `IOError` this process
    encounters is logged (tracked via the module-level `_io_error_already_logged` flag) — a
    persistently unwritable log file would otherwise re-log the same failure on every single
    tool call for the rest of the process's life.
    """
    global _io_error_already_logged
    try:
        path = Path.cwd() / TOOL_CALLS_LOG_FILENAME
        file_has_contents = path.is_file() and path.stat().st_size > 0

        request_json = json.dumps({"name": name, "arguments": args}, indent=2, default=str)
        response_payload = {"error": error} if error is not None else {"result": result}
        response_json = json.dumps(response_payload, indent=2, default=str)

        entry_lines = [
            "---", datetime.now().isoformat(), "Request:", request_json, "Response:", response_json]
        if file_has_contents:
            entry_lines.insert(0, "")

        with path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n".join(entry_lines) + "\n")
    except IOError as exc:
        if not _io_error_already_logged:
            _io_error_already_logged = True
            logger.error("Failed to write tool call log entry to %s: %s", TOOL_CALLS_LOG_FILENAME, exc)
