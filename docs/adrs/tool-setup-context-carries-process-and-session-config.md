# Give every Tool a ToolSetupContext holding ProcessConfig and SessionConfig, not pre-extracted settings

* Date: 2026-07-01 16:45
* Question: `Tool` implementations (`klorb/src/klorb/tools/`) need to be constructed
  uniformly by `ToolRegistry`, but some of them need to configure themselves from settings
  that live outside the tool itself — e.g. `ReadFileTool`'s per-call line cap, which is
  `ProcessConfig.read_file_max_lines`. Should every `Tool` be constructed with a single
  context object carrying whatever config it might need, and if so, should that context hold
  the actual `ProcessConfig`/`SessionConfig` objects, or should it pre-extract just the
  individual settings tools happen to use today (e.g. a flat `read_file_max_lines: int`
  field)?
* Answer: Every concrete `Tool` is constructed with one `klorb.tools.setup_context.ToolSetupContext`
  argument (enforced by `Tool.__init__`, see `klorb/src/klorb/tools/tool.py`), never
  tool-specific constructor arguments. `ToolSetupContext` holds references to the actual
  `process_config: ProcessConfig` and `session_config: SessionConfig` objects, not
  individually pre-extracted settings — a tool reads straight off
  `context.process_config.read_file_max_lines` rather than a `ToolSetupContext.read_file_max_lines`
  field. `session_config` is the *live* `Session.config`, not `process_config.session` (which
  is only the template a new session's config is copied from — see
  [the ProcessConfig/SessionConfig nesting ADR](nest-sessionconfig-inside-a-process-scoped-processconfig.md)
  — and won't reflect changes made to the live session, e.g. via the TUI command palette).
* Reasoning: Pre-extracting each setting a tool needs onto `ToolSetupContext` would mean every
  new tool-specific setting requires both a `ProcessConfig`/`SessionConfig` field *and* a
  matching `ToolSetupContext` field kept in sync by hand — a second, redundant place for the
  same value to drift out of date. Holding the actual config objects means adding a new
  setting a tool needs is exactly the same one-field change as adding any other process/session
  setting; `ToolSetupContext` itself never needs to change. This does mean
  `klorb.tools.setup_context` depends on `klorb.process_config`, which in turn depends on
  `klorb.session` (for `SessionConfig`) — and `klorb.process_config` previously imported
  `klorb.tools.read_file.MAX_LINES` for its own default value, which would have made this
  import circular (`setup_context -> process_config -> read_file -> tool -> setup_context`).
  The fix wasn't to duplicate the constant (that was tried once and reverted — see
  `git log` on `process_config.py`'s `DEFAULT_READ_FILE_MAX_LINES`) but to flip which module
  owns it: `process_config.py`'s `DEFAULT_READ_FILE_MAX_LINES` is now the *only* definition,
  and `klorb.tools.read_file` has no constant of its own at all (it reads
  `context.process_config.read_file_max_lines` at construction time, same as any other
  process-config-backed tool setting). Since `ProcessConfig` never imports from `klorb.tools`
  in the first place — the whole point of this ADR — nothing loops back, and there's exactly
  one place this default can drift out of date: nowhere, because there's only one copy.
