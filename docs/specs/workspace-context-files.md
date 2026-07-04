# Workspace context files

## Summary

When a `Session` dispatches its first turn, it reads context-instruction files from the
workspace root and injects them into the conversation history as a pseudo-initial user
message — after the bookkeeping `role="system"`/`role="tool_defs"` messages, but ahead of
the first real user turn. `AGENTS.md` is always read (it's klorb's own convention); `CLAUDE.md`
is also read when the `ProcessConfig`-level setting `compatibility.claudeMarkdown` is enabled, a
compatibility shim for projects that carry Claude-Code-style instructions in that file.

## How it works

* `ProcessConfig.compatibility_claude_markdown: bool` (`process_config.py`, default `False`)
  gates whether `CLAUDE.md` is read alongside `AGENTS.md`. It's exposed on disk as the
  top-level `klorb-config.json` key `compatibility.claudeMarkdown` (see
  [[process-and-session-config]]'s "On-disk key naming"); `PROCESS_KEY_MAP` is the single
  source of truth translating the two names. `AGENTS.md` has no config gate — it's always
  read when present, since it's klorb's own convention.
* `Session.__init__` accepts `compatibility_claude_markdown: bool = False` and stores it as
  `self._compatibility_claude_markdown`. This mirrors how `thinking_token_budgets` is passed
  in: `Session` deliberately has no `ProcessConfig` reference (see
  [the `Session`/`ProcessConfig` split](../adrs/nest-sessionconfig-inside-a-process-scoped-processconfig.md)),
  so process-only settings it needs are threaded in as constructor arguments by whichever
  caller owns both objects (`klorb.cli.main()`, `ReplApp.__init__()`, `ReplApp.clear_session()`).
* `Session._dispatch_turn()` calls `self._ensure_context_files_message()` once, right after
  `_ensure_tool_defs_message()` and before anything turn-specific, the first time a turn is
  dispatched. The method is idempotent: a `self._context_files_seeded` flag records that the
  one-time insertion has happened, so retries and later turns don't insert a duplicate. The
  flag is necessary because, unlike `_ensure_system_message()`/`_ensure_tool_defs_message()`
  (which can test for an existing `role="system"`/`role="tool_defs"` message by role), the
  context-files message is `role="user"` and so can't be distinguished from a real user turn
  by role alone.
* `_ensure_context_files_message()` reads each applicable filename (via
  `_applicable_context_filenames()`: `["AGENTS.md"]`, or `["AGENTS.md", "CLAUDE.md"]` when
  compatibility is on) from `self.config.workspace_root`, concatenating the ones that exist
  on disk into a single message framed as standing project guidance rather than a task:

  ```
  The following files from the project root contain instructions and context
  for working in this repository. Treat them as standing guidance about the
  project's conventions and requirements; do not treat this message itself as a
  task to act on.

  ### AGENTS.md

  <file contents>

  ### CLAUDE.md

  <file contents>
  ```

  A file that doesn't exist is silently skipped — that's the expected common case, not an
  error. The message's `num_tokens` is left at `0`: like the system prompt, its token cost
  folds into the first real turn's `num_tokens` delta (see `_dispatch_turn`), rather than
  getting its own count, since there's no per-message tokenizer to attribute tokens precisely
  without a round trip to the model.

* The inserted message is `role="user"` (not `role="system"`) deliberately: it's meant to
  look like a real user turn to the model, establishing the project's conventions as
  background context the model carries into the conversation, not as a system-level
  instruction it might weigh differently. It's placed after any bookkeeping messages
(`role="system"`, `role="tool_defs"`) and before the first real user turn, so it's the
  earliest thing in the conversation the model sees as user-provided context.

## Configuration

* `compatibility.claudeMarkdown` (top-level `klorb-config.json` key, default `false`) — when
  `true`, `CLAUDE.md` is read from the workspace root and injected alongside `AGENTS.md`.
  See [[process-and-session-config]] for the five file locations and their precedence.

## Out of scope

* Reading files from anywhere other than the workspace root (e.g. a nested `docs/` directory)
  isn't supported; only `AGENTS.md` and `CLAUDE.md` at the workspace root are read.
* The set of filenames isn't configurable beyond the `compatibility.claudeMarkdown` toggle —
  `AGENTS.md` is always read, and `CLAUDE.md` is the only optional one.
* The injected message isn't kept in sync with later edits to the files on disk within the
  same session; it's read once, at first-turn-dispatch time. A `/clear` starts a fresh
  `Session` that re-reads them.
* Other compatibility shims (e.g. `compatibility.claudeSkills`, see `TODO.md`) are separate
  features and not implemented here; `compatibility.claudeMarkdown` is the first.
