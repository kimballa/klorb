# Session applies and persists a permission grant itself, not the TUI callback that asked for it

* Date: 2026-07-03 00:00
* Question: When an interactive "ask" permission prompt resolves to a persistent scope
  (`"session"`/`"workspace"`/`"homedir"`), something has to call
  `klorb.permissions.grant.apply_permission_grant()` — which needs both the live `SessionConfig`
  and, for `"workspace"`/`"homedir"`, a `ProcessConfig` to ripple the grant into (so a future
  `/clear` inherits it) and to persist it to disk. That used to live in
  `ReplApp._on_permission_ask` (`klorb.tui.repl`), the TUI's implementation of `Session`'s
  `on_permission_ask` callback, because `Session` itself had no `ProcessConfig` reference at all
  — only the App layer did. Now that `Session.__init__` accepts a `process_config` argument (see
  [the ProcessConfig-ctor-arg ADR](session-takes-a-processconfig-ctor-arg-not-preextracted-fields.md)
  — a preceding, narrower change that gave `Session` a `ProcessConfig` reference purely to read
  `thinking_token_budgets`/`compatibility_claude_markdown` off it), does the grant-persisting
  responsibility still belong in the TUI callback, or should it move into `Session`?
* Answer: `Session._retry_after_permission_decision` now calls `apply_permission_grant()` itself
  — passing `self.config` and `self._process_config` (a new attribute: the `ProcessConfig` this
  `Session` was constructed with, kept by reference, not just pre-extracted into a couple of
  fields as before) — for `"session"`/`"workspace"`/`"homedir"` decisions, before retrying the
  call. `apply_permission_grant()`'s `process_config` parameter became `ProcessConfig | None`: it
  still always promotes the live `SessionConfig` and persists `"workspace"`/`"homedir"` grants to
  disk (`project_config_path()`/`user_config_path()`), since neither of those needs the
  in-memory `ProcessConfig` object — but it now skips the `process_config.session.*` ripple step
  entirely when `process_config` is `None`, rather than requiring every caller to have one.
  `ReplApp._on_permission_ask` shrank to just showing the modal and returning the user's
  `PermissionDecision` — it no longer imports or calls `apply_permission_grant()` at all.
* Reasoning: The old split existed only because `Session` had no `ProcessConfig` reference to
  work with; once it gained one, leaving the persistence logic in the TUI callback would have
  meant `ReplApp` reaching into `klorb.permissions.grant` and stitching together
  `session.config`/`process_config` itself — exactly the kind of agentic/business logic this
  repo's CLI/library firewall (see `CLAUDE.md`'s "subprojects" section) says shouldn't live
  outside the library, and something any other consumer of `Session` (a future VSCode plugin,
  say) would have had to reimplement identically to get persistent grants at all. Moving it into
  `Session` means `on_permission_ask` implementations only ever need to *ask* — never apply or
  persist anything — which is also a strictly smaller contract to satisfy for a new caller.
  Making `apply_permission_grant()`'s `process_config` parameter optional (rather than, say,
  requiring every `Session` to be handed one) matters because not every `Session` construction
  site necessarily has a `ProcessConfig` in hand — direct library use, or a test — and a `None`
  there degrading gracefully (skip the in-memory ripple, keep the parts that don't need it) is
  more useful than forcing every such caller to fabricate a throwaway `ProcessConfig()` just to
  satisfy the signature.
