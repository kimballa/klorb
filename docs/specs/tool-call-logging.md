# Tool call logging

## Summary

klorb can keep an out-of-band, file-based audit trail of every tool call a `Session` executes:
one entry per call, appended to `tool-calls.log` in the current working directory, carrying the
call's name/arguments (its "request") and its result or error (its "response"). This is
deliberately independent of klorb's ordinary `logging`-module instrumentation (see
[[paths-and-logging]]) — it's always a direct file append, never routed through a `Logger` or
`Handler`, so it keeps working (or not) regardless of the active log level or handler
configuration. Disabled by default; enabled by any of three independent sources.

## How it works

* `klorb.tool_call_log` (`klorb/src/klorb/tool_call_log.py`) owns the whole feature:
  * `log_tool_call(name: str, args: dict[str, Any], result: Any, error: str | None) -> None`
    appends one entry to `Path.cwd() / "tool-calls.log"` (`TOOL_CALLS_LOG_FILENAME`), opening it
    in append mode (`"a"`) so it's created if it doesn't exist yet and never truncated if it
    does. An entry is:

    ```
    ---
    2026-07-08T14:03:21.123456
    Request:
    {
      "name": "ReadFile",
      "arguments": {
        "filename": "foo.py"
      }
    }
    Response:
    {
      "result": {...}
    }
    ```

    — a `---` divider line, an ISO-8601 timestamp (`datetime.now().isoformat()`), `"Request:"`
    followed by `{"name": ..., "arguments": ...}` as pretty-printed JSON (`indent=2`,
    `default=str` so a non-JSON-native argument value still renders instead of raising), then
    `"Response:"` followed by `{"result": ...}` on success or `{"error": ...}` on failure — the
    same success/failure discriminant `klorb.session.ToolCallEvent` uses (`error is not None`
    means the call failed and `result` is meaningless). When the file already has contents (a
    prior entry from this run, or a previous run — the file is never rotated or cleared), a
    blank line is written first to separate the new entry from what's already there; the very
    first entry in a fresh file has no leading blank line.

    `log_tool_call()` never raises: an `IOError` (e.g. an unwritable working directory, a full
    disk) is caught and reported via `logger.error()` instead of propagating, since this is a
    best-effort audit trail — a tool call's own success must never depend on whether its log
    entry could be written. Only the *first* `IOError` encountered by the process is logged
    (tracked by the module-level `_io_error_already_logged` flag, set once and never reset);
    every subsequent call that hits the same (or a different) `IOError` fails silently, so a
    persistently unwritable log file doesn't re-report the same failure on every single tool
    call for the rest of the process's life.
  * `tool_call_logging_enabled(config_enabled: bool | None) -> bool` resolves whether logging
    is active: when `config_enabled` is an explicit `True` or `False`, that value is
    authoritative; only when it is `None` (neither the config key nor a CLI flag set it) does
    the function fall back to the `LOG_TOOL_CALLS` environment variable, returning `True` if
    it is `"1"` or `"true"` (case-insensitive, via `_env_var_truthy()`). The environment
    variable is read at call time (not cached at import time), so a `.env` file loaded by
    `klorb.cli.main()` before this is checked is honored.
* `ProcessConfig.log_tool_calls: bool | None` (`process_config.py`, default `None`) is the
  config-file/CLI-flag-driven half of activation. Exposed on disk as the top-level
  `klorb-config.json` key `tools.logCalls` (`LOG_TOOL_CALLS_CONFIG_KEY`, mapped via
  `PROCESS_KEY_MAP`, same as every other process-only setting — see
  [[process-and-session-config]]).
* `klorb.cli.main()`'s `--log-tool-calls`/`--no-log-tool-calls` flag pair
  (`argparse.BooleanOptionalAction`, default `None`) sets
  `process_config.log_tool_calls` to `True` or `False` respectively when either is passed.
  Omitting both leaves `process_config.log_tool_calls` at whatever the config file
  resolved (or `None`), so `--no-log-tool-calls` can override a config file's `true` back
  to `false` (unlike the purely-additive `-y`/`--auto-approve`).
* `Session.__init__` resolves the three sources into one boolean once, at construction time:
  `self._log_tool_calls = tool_call_logging_enabled(process_config.log_tool_calls if
  process_config is not None else None)`. This mirrors how `compatibility_claude_markdown` is
  pulled out of `process_config` in `__init__` (see [[workspace-context-files]]) — `Session`
  never holds a `ProcessConfig` reference itself (see
  [the `Session`/`ProcessConfig` split](../adrs/nest-sessionconfig-inside-a-process-scoped-processconfig.md)),
  so this is read once and cached as a plain `bool` for the session's lifetime. A `Session`
  constructed with no `process_config` at all (e.g. most unit tests) still honors
  `LOG_TOOL_CALLS`, since `tool_call_logging_enabled()` checks it unconditionally.
* `Session._run_tool_calls()` calls `log_tool_call(call.name, args, result, error)` once per
  dispatched call, right after computing that call's `content`/before firing
  `callbacks.on_tool_call` — the same place `ToolCallEvent` is built, so both consumers see
  the same `result`/`error` outcome, but the log entry is written unconditionally (whenever
  `self._log_tool_calls` is set) rather than only when a caller happens to supply
  `callbacks.on_tool_call`.

## Configuration

* `tools.logCalls` (top-level `klorb-config.json` key, default unset) — enables or disables
  tool call logging for every session in this process. Sets `ProcessConfig.log_tool_calls`
  to an explicit `True`/`False`.
* `--log-tool-calls` / `--no-log-tool-calls` — CLI flag pair (`BooleanOptionalAction`,
  default `None`); sets `ProcessConfig.log_tool_calls` to `True`/`False` respectively,
  overriding the config key. Omitting both leaves the config-key value (or `None`) in place.
* `LOG_TOOL_CALLS` (environment variable, `"1"` or `"true"`, case-insensitive) — enables
  tool call logging only when `ProcessConfig.log_tool_calls` is still `None` (i.e. neither
  the config key nor a CLI flag set it), checked by `tool_call_logging_enabled()`.

The CLI flag (applied after config loading) and the config key both resolve to an explicit
`True`/`False` on `ProcessConfig.log_tool_calls`, which is authoritative — the environment
variable is consulted only as the fallback when that field is still `None`. So
`--no-log-tool-calls` overrides `LOG_TOOL_CALLS=true`, and `tools.logCalls: true` overrides
`LOG_TOOL_CALLS` the same way.

## Out of scope

* `tool-calls.log` is never rotated, size-capped, or pruned — every run appends to whatever is
  already there, indefinitely, mirroring `session-logs/`'s own lack of pruning (see
  [[paths-and-logging]]).
* The log file's location isn't configurable: it's always `tool-calls.log` in the current
  working directory, not under `$KLORB_STATE_DIR` alongside session logs — this is meant as an
  ad hoc, per-invocation-directory debugging aid, not process-wide state.
* There's no way to log only a subset of tool calls (by name, by success/failure, etc.) — when
  active, every dispatched call is logged.
