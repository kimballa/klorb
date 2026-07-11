# AskUserQuestions bypasses the deny/ask/allow permission tables entirely

* Date: 2026-07-10 00:00
* Question: Every other tool that needs user confirmation (`ReadFile`/`EditFile` against
  `readDirs`/`writeDirs`, `BashTool` against `commandRules`) computes a `deny`/`ask`/`allow`
  verdict from a `PermissionsTable` before acting, and only raises `PermissionAskRequired`/
  `MultiPermissionAskRequired` for the `"ask"` case. `AskUserQuestionsTool` also needs to
  suspend the tool call and wait on the user, via the same `Session._run_tool_calls`
  catch-and-callback mechanism (see docs/plans/archive/007-ask-user-questions-tool.md). Should
  it get a `tools.askUserQuestions.*Permission` config entry and a `PermissionsTable`-style
  verdict computation of its own, consistent with every other interactive tool — or should it
  skip that machinery outright, the way the scratchpad tools do (see
  docs/adrs/scratchpad-tools-bypass-permission-tables.md)?
* Answer: Skip it outright. `AskUserQuestionsTool.apply()` raises
  `AskUserQuestionsRequired` unconditionally for any well-formed `questions` argument — there
  is no verdict to compute, no `deny`/`ask`/`allow` table consulted, and no
  `ProcessConfig`/`SessionConfig` field governing it. `Session._resolve_ask_user_questions`
  resolves the exception directly against `callbacks.on_ask_user_questions`, with no
  `SessionConfig.permission_framework` branching either: a headless run (no callback given)
  always fails the call closed, and an interactive run always shows the prompt.
* Reasoning: `PermissionsTable`/`deny`/`ask`/`allow` exist to gate a model's access to a
  resource it could otherwise reach on its own — a filesystem path or shell command the user
  hasn't vetted. `permission_framework`'s `"deny"`/`"ask"`/`"auto"` axis exists on top of that
  to express the *user's* risk tolerance for auto-resolving those verdicts non-interactively.
  Neither concept applies here: asking the user a question is not a resource access, so there
  is nothing to pre-allow, and there is no meaningful "auto-approve" for a question that only
  the user can actually answer — synthesizing an answer would defeat the tool's entire
  purpose. The interactivity itself is the only gate this tool has: an interactive session
  always shows the prompt; a headless one has nobody to ask, so it fails closed
  unconditionally, the same way an unhandled `PermissionAskRequired` does with no callback.

  This mirrors the scratchpad tools' bypass in outcome (no permission-table entry) but for a
  different reason: the scratchpad tools bypass the tables because their target path is
  harness-fixed, never model-supplied, so there's nothing for a table to protect.
  `AskUserQuestionsTool` has no path or command argument at all to protect in the first
  place — its bypass is because the operation is inherently interactive, not because its
  resource is harness-owned.
