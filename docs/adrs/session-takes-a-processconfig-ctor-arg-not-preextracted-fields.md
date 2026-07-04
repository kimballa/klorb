# Session's constructor takes a ProcessConfig reference, not one ctor arg per field it needs off it

* Date: 2026-07-03 00:00
* Question: `Session.__init__` needed two settings that only live on `ProcessConfig`
  (`thinking_token_budgets`, `compatibility_claude_markdown`), but `klorb.session` can't import
  `klorb.process_config` for real at module scope — `ProcessConfig` itself depends on
  `SessionConfig`/`ThinkingEffort`/`THINKING_EFFORT_TOKEN_BUDGETS` from `klorb.session`, so a
  real top-level import the other way would be circular. Should each call site
  (`klorb.cli.main()`, `ReplApp.__init__`/`clear_session()`) keep unpacking the individual fields
  it needs and passing them as their own keyword arguments, or should `Session` take the whole
  `ProcessConfig` object as one constructor argument instead?
* Answer: `Session.__init__` takes a single `process_config: ProcessConfig | None = None`
  argument. The type is resolved via a `TYPE_CHECKING`-only import (the same pattern already
  used for `ToolRegistry`, for the identical reason — see
  [the ToolSetupContext ADR](tool-setup-context-carries-process-and-session-config.md)), so
  nothing is imported for real at module scope and the circularity never manifests. `Session`
  extracts `thinking_token_budgets`/`compatibility_claude_markdown` off it at construction time
  (falling back to defaults when `process_config` is `None`), and — see
  [the permission-grant ADR](session-applies-its-own-permission-grants.md) — also keeps the
  `ProcessConfig` reference itself around as `self._process_config`, for logic that needs the
  live object rather than a value snapshotted at construction time.
* Reasoning: Passing individual pre-extracted fields (`thinking_token_budgets=...,
  compatibility_claude_markdown=...`) means every call site has to know and repeat the exact set
  of `ProcessConfig` fields `Session` currently cares about, and every future field `Session`
  needs gets a new constructor parameter *and* a new line at every call site kept in sync by
  hand — the same anti-pattern the `ToolSetupContext` ADR already rejected for `Tool`
  construction. Passing the whole object once means a new setting `Session` needs later is a
  one-line change inside `Session` itself, not a signature change rippling out to every caller.
