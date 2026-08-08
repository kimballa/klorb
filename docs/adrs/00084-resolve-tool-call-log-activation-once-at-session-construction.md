# Resolve tool-call-log activation once, at `Session` construction

* 2026-07-08 14:00

## Question

Tool call logging (see [[tool-call-logging]]) can be turned on by a config key, a CLI flag, or
the `LOG_TOOL_CALLS` environment variable. Where should these three sources be combined into the
one boolean that actually gates `Session._run_tool_calls()`'s per-call file write: re-evaluated
on every tool call, or resolved once?

## Answer

Resolve once, in `Session.__init__`, and cache the result as `self._log_tool_calls`. `klorb.cli.
main()` folds the config key and CLI flag into `ProcessConfig.log_tool_calls` before constructing
`Session`; `Session.__init__` then combines that with the environment variable via
`tool_call_logging_enabled()` and stores the single resulting `bool`.

## Reasoning

None of the three sources can change mid-session: the config key and CLI flag are fixed at
process start, and nothing in klorb re-reads `LOG_TOOL_CALLS` from the environment after startup
either. Re-checking `os.environ` on every dispatched tool call would just repeat the same lookup
for no behavioral difference, while resolving once keeps `_run_tool_calls()`'s hot path a plain
`if self._log_tool_calls:` check — the same pattern already used for
`self._compatibility_claude_markdown` (see [[workspace-context-files]]), so this doesn't
introduce a second convention for pulling a process-only setting into `Session`.

The environment variable is still checked via `tool_call_logging_enabled()` inside
`klorb.tool_call_log` rather than folded into `ProcessConfig` at config-load time, so that a
caller constructing a `Session` directly (without going through `klorb.cli.main()` or even a
`ProcessConfig` at all — e.g. most unit tests) still gets `LOG_TOOL_CALLS` honored, rather than
silently losing that activation path outside the CLI entry point.
