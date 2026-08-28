# 00210: Freeze a session's tool-definitions wire block at first dispatch

**Date:** 2026-08-28

**Question:** `Session._dispatch_turn()` calls `ToolRegistry.tool_definitions()` fresh on every
turn to build the `tools` array sent to the model. Every existing tool's schema is static, so
this has never mattered. `WebSearchTool` (a new `ServerTool`) reads its `excluded_domains` from
`session_config.web_domain_rules.deny`, which the user can change mid-session (a permission
grant, a TUI palette edit) — so without a fix, the `tools` block would start varying turn to
turn, busting the provider's prompt cache on the entire request every time it changed. Where
should this get frozen, so the block stays stable once the first request goes out?

**Answer:** Freeze in `Session`, not in `ToolRegistry`. `Session._tool_definitions_for_dispatch()`
computes `tool_registry.tool_definitions()` once, caches the result on
`Session._frozen_tool_definitions`, and returns that cached list on every later call for the
rest of the session's lifetime. `_dispatch_turn()` calls this instead of
`tool_registry.tool_definitions()` directly. `ToolRegistry.tool_definitions()` itself is left
uncached.

**Reasoning:** `ToolRegistry.tool_definitions()` is called from more than just the wire-send
path — a UI tool-list panel and `SearchTools` both want it to reflect current config, not a
snapshot from turn one. Caching inside `ToolRegistry` would freeze those callers too, the moment
anything (even a passive inspection) happened to call it before the session's real first turn.
Freezing in `Session` scopes the cache to exactly the one call site that needs stability — the
literal payload handed to `_provider.send_prompt()` — without touching `ToolRegistry`'s general
contract. A fresh `Session` (e.g. from `/clear`) naturally starts unfrozen again, since
`_frozen_tool_definitions` is a per-instance field initialized to `None` in `Session.__init__`.
