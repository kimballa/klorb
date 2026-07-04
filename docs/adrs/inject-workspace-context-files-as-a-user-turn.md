# Inject workspace context files as a user turn, not a system message

## 2026-06-30

## Question

Should the workspace's context-instruction files (`AGENTS.md`, and `CLAUDE.md` when
`compatibility.claudeMarkdown` is enabled) be injected into the conversation as a
`role="system"` message or a `role="user"` message?

## Answer

`role="user"`.

## Reasoning

`Session._ensure_system_message()` already inserts a `role="system"` bookkeeping message
recording the resolved system prompt, and `OpenRouterApiProvider._build_api_messages()`
drops `role="system"` messages from the wire-format history — the live system prompt is sent
via `send_prompt(system_prompt=...)` on every turn instead, re-resolved fresh each time so it
reflects the current active model. A context-files message injected as `role="system"` would
be dropped the same way and never reach the model as conversation history.

More fundamentally, the context files are meant to establish the project's conventions as
background the model carries into the conversation — the same role a user's own opening
message plays, not a system-level instruction the model might weigh differently. Injecting
them as `role="user"` (placed ahead of the first real user turn, after the bookkeeping
messages) makes them part of the conversation the model actually sees and reasons over,
which is the intent.

The one wrinkle is idempotency: `_ensure_system_message()`/`_ensure_tool_defs_message()` test
for an existing `role="system"`/`role="tool_defs"` message to avoid duplicate insertion. A
`role="user"` message can't be told apart from a real user turn by role alone, so
`_ensure_context_files_message()` tracks its own `self._context_files_seeded` flag instead.
