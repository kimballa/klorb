# AskUserQuestions

## Summary

`AskUserQuestions` (`klorb.tools.ask.ask_user_questions.AskUserQuestionsTool`) lets the agent pose
one or more structured questions to the user instead of guessing at an ambiguous requirement,
inventing a plausible default, or re-deriving the same uncertain conclusion in a loop. Each
question offers 0, or 2-5, fixed multiple-choice options (one may be flagged `recommended`)
plus an always-available free-text "Other" answer; several independent questions can be asked
in a single call.

## Tool arguments and result

```json
{
  "questions": [
    {
      "header": "Auth method",
      "question": "Which auth method should new endpoints use?",
      "options": [
        {"label": "JWT bearer token", "description": "Stateless, works across services.", "recommended": true},
        {"label": "Session cookie", "description": "Simpler, but ties auth to this service."}
      ]
    }
  ]
}
```

* `header` — a short chip-style label (roughly 12 characters or fewer).
* `question` — the full question text.
* `options` — either empty (the user is shown a free-text box with no listed choices) or
  2-`MAX_MULTI_CHOICE_OPTIONS` (5) entries, each with a required `label` and optional
  `description`. Exactly one listed option is invalid — offer at least 2, or 0.
* `recommended: true` may be set on at most one option per question, and only on the first
  option (index 0) — the model must put its recommended option first; a `recommended` flag on
  any later option is rejected as a validation error. This is purely a display hint (the TUI
  badges it `"(Recommended)"`); it is never auto-selected on the user's behalf.
* A free-text "Other" choice is always available to the user in addition to any listed
  `options` — it is never itself one of them, and the model should not add its own catch-all
  option.

The result, once every question is answered, is:

```json
{
  "answers": [
    {"header": "Auth method", "question": "Which auth method...?", "answer": "JWT bearer token: Stateless, works across services."}
  ]
}
```

`answer` is a single rendered string per question: `"label: description"` for a selected
option with a description (just `"label"` if it has none — see
`klorb.tools.ask.common.format_answer`), or the user's raw free-text answer
verbatim, whether typed via "Other" or because the question had zero listed `options` to begin
with.

## Interactive flow

`AskUserQuestionsTool.apply()` validates its arguments and, for any well-formed `questions`
list, always raises `AskUserQuestionsRequired` (`klorb.tools.ask.common`) rather
than returning a value — asking *is* the tool's entire job, so there is no other outcome to
compute. This mirrors the permission-ask mechanism's shape (`PermissionAskRequired` /
`PermissionAskContext`/`PermissionDecision` — see this file's counterpart,
docs/specs/permissions.md's "Interactive 'ask' confirmation" section), reusing the same
catch-in-`Session`-and-callback plumbing rather than inventing a second mechanism:

* `Session._run_tool_calls()` catches `AskUserQuestionsRequired` and hands it to
  `Session._resolve_ask_user_questions()`, which asks about each question in turn via
  `TurnEventHandlers.on_ask_user_questions` (`klorb.session.AskUserQuestionsItemContext ->
  klorb.session.AskUserQuestionsAnswer`), collecting one answer per question into the tool's
  final `result["answers"]`.
* Unlike a permission ask, there is **no** `SessionConfig.permission_framework` branching:
  asking the user is not a resource-access verdict an `"auto"`/`"deny"` risk-tolerance setting
  applies to. With no `on_ask_user_questions` callback given (e.g. a headless one-shot run —
  see `Session.run_one_shot()`), the call fails closed unconditionally, the same as an
  unhandled `PermissionAskRequired` with no callback — there is nobody to ask, and there is no
  sensible "auto-answer" fallback.
* `AskUserQuestionsAnswer.cancelled` (set when the user presses Escape in the TUI) has no
  deny/allow axis to fall back to the way `PermissionDecision` does: it short-circuits the
  rest of the batch and fails the whole tool call, naming the cancelled question and echoing
  back any answers already collected for earlier questions in the same call, together with a
  reminder not to keep guessing or re-asking the same thing a different way.
* `ReplApp._on_ask_user_questions`/`_confirm_ask_user_questions` (`klorb.tui.repl`) is the
  TUI's implementation, mirroring `_on_permission_ask`/`_confirm_permission_ask`: it blocks the
  worker thread running `Session.send_turn()` via `call_from_thread`, shows
  `klorb.tui.ask_user_questions_screen.AskUserQuestionsScreen` for one question at a time on
  the app's own event loop, and returns once the user answers.

### `AskUserQuestionsScreen`

One question per modal (rather than one combined multi-question screen), matching how a
`MultiPermissionAskRequired`'s items are each shown via their own `PermissionAskScreen`. A
single-column, Up/Down-navigable list of the question's `options` (the first one badged
`"(Recommended)"` when marked so) plus a trailing "Other..." row, always present. Confirming an
option with Enter dismisses with that option's rendered `answer`; confirming "Other..." (or
pressing `o`, a fast path from any row) reveals a free-text `Input` whose submission dismisses
with the typed text as `answer`. Escape dismisses with `cancelled=True` from any state,
including from inside the revealed `Input`. A question with zero `options` skips the list
entirely and reveals the `Input` immediately on mount, since there's nothing else to navigate
to.

## Why this bypasses the permission tables

`AskUserQuestionsTool` has no `tools.askUserQuestions.*Permission` config entry and consults no
`deny`/`ask`/`allow` table — see
docs/adrs/ask-user-questions-tool-bypasses-permission-tables.md. The tables exist to gate a
model's access to a resource (a path, a shell command) it could otherwise reach directly; there
is no such resource here, so there's nothing to pre-allow or deny. The interactivity itself is
the only gate: an interactive session always shows the prompt, and a headless one fails closed
unconditionally.

## System prompt guidance

`klorb/src/klorb/resources/system_prompts.d/default_sys.md`'s `<ask_user_questions>` section
tells the agent to reach for this tool instead of silently picking an interpretation or
inventing a plausible default, and — the same section — to treat noticing itself spinning
(re-deriving the same uncertain conclusion, second-guessing a settled choice, trying several
framings of the same question to itself without resolving it) as the signal to stop and ask,
not to think harder. The `<minimal_changes>` section's ambiguous-task guidance draws the line
between low-stakes ambiguity (guess, and note the assumption in the reply) and consequential
ambiguity — hard to reverse, affecting data or shared systems, or where the interpretations
would lead to meaningfully different work — which should go through `AskUserQuestions` instead.
