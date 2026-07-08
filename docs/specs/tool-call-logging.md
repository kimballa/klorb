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
  * `tool_call_logging_enabled(config_enabled: bool) -> bool` resolves whether logging is
    active: `True` if `config_enabled` is `True`, or if the `LOG_TOOL_CALLS` environment
    variable is `"1"` or `"true"` (case-insensitive, via `_env_var_truthy()`) — read at call
    time (not cached at import time), so a `.env` file loaded by `klorb.cli.main()` before this
    is checked is honored. `config_enabled` is expected to already be the OR of the
    `tools.logCalls` config key and the `--log-tool-calls` CLI flag (see below) — this function
    only adds the environment variable as a third, independent activation source.
* `ProcessConfig.log_tool_calls: bool` (`process_config.py`, default `False`) is the
  config-file/CLI-flag-driven half of activation. Exposed on disk as the top-level
  `klorb-config.json` key `tools.logCalls` (`LOG_TOOL_CALLS_CONFIG_KEY`, mapped via
  `PROCESS_KEY_MAP`, same as every other process-only setting — see
  [[process-and-session-config]]).
* `klorb.cli.main()`'s `--log-tool-calls` flag (`store_true`, default `False`) sets
  `process_config.log_tool_calls = True` when passed — additive, like `-y`/`--auto-approve`: it
  can only turn logging on, never override a config file's `true` back to `false`.
* `Session.__init__` resolves the three sources into one boolean once, at construction time:
  `self._log_tool_calls = tool_call_logging_enabled(process_config.log_tool_calls if
  process_config is not None else False)`. This mirrors how `compatibility_claude_markdown` is
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

* `tools.logCalls` (top-level `klorb-config.json` key, default `false`) — enables tool call
  logging for every session in this process.
* `--log-tool-calls` — CLI flag; enables tool call logging for this run regardless of
  `tools.logCalls`'s value.
* `LOG_TOOL_CALLS` (environment variable, `"1"` or `"true"`, case-insensitive) — enables tool
  call logging regardless of the config key or CLI flag, checked independently by
  `tool_call_logging_enabled()`.

All three are OR'd together — there is no precedence between them (unlike the layered
config-file merge described in [[process-and-session-config]]); any one of them being "on" is
enough to activate logging.

## Out of scope

* `tool-calls.log` is never rotated, size-capped, or pruned — every run appends to whatever is
  already there, indefinitely, mirroring `session-logs/`'s own lack of pruning (see
  [[paths-and-logging]]).
* The log file's location isn't configurable: it's always `tool-calls.log` in the current
  working directory, not under `$KLORB_STATE_DIR` alongside session logs — this is meant as an
  ad hoc, per-invocation-directory debugging aid, not process-wide state.
* There's no way to log only a subset of tool calls (by name, by success/failure, etc.) — when
  active, every dispatched call is logged.
