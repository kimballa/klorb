
# AskUserQuestions Tool

A tool the agent calls to pose one or more structured, multiple-choice questions to the
user instead of guessing, fabricating an assumption, or spinning through the same
reasoning in circles. Each question offers 2-4 fixed options (one may be flagged
`recommended`, and is always shown first) plus an ever-present, unlisted "Other: ___"
free-text option. The agent can ask several questions in one call so a single round trip
can resolve several independent unknowns at once.

This is the same interactive-confirmation *shape* the codebase already has for permission
asks (`klorb.permissions.table.PermissionAskRequired`, `klorb.session.PermissionAskContext`/
`PermissionDecision`, `klorb.tui.permission_ask_screen.PermissionAskScreen`) — a tool's
`apply()` can't itself block on user input (see `Tool`'s "single `ToolSetupContext`
argument" contract in `klorb/src/klorb/tools/tool.py`), so it raises, `Session._run_tool_calls`
catches, an event-handler callback gets the answer, and the answer becomes the tool's
result. This plan reuses that mechanism end-to-end rather than inventing a second one —
see "Interactive flow" below for exactly which classes are mirrored vs. new.

## The tool

New module `klorb/src/klorb/tools/ask_user_questions.py` (single file — no subpackage;
per `ToolRegistry._discover_tools()`, a subpackage like `tools/memory/` or
`tools/scratchpad/` is only warranted for a *family* of related tools sharing common
helpers, and this is one tool).

### Parameters

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

* `header`: short chip label (roughly ≤12 chars), shown as a small title/breadcrumb —
  e.g. `"Q2/3: Auth method"` when several questions are asked in one call.
* `question`: the full question text, ending in `?`.
* `options`: zero, or ~2-4 entries. `label` is required; `description` is optional (a short
  clarifying line). At most one option per question may set `recommended: true` —
  `parameters()`/`apply()` reject a `questions` payload with more than one `recommended`
  option in the same question with a validation error back to the model, same as any
  other malformed-arguments case. The label is always rendered in bold, along a `:`, e.g.:

  ```text
  **Session cookie:** Simpler, but ties auth...
  ```

  * The agent is free to produce zero options. In which case we just ask the question and give
    the user a text box where they are expected to reply.
  * It is an error to produce exactly one option plus the implicit 'other'.
  * ask_user_questions.py includes a MAX_MULTI_CHOICE_OPTIONS=5 value. If the agent produces more
    options than this for a question, that is an error and the agent should be
    admonished to constrain the set of options more clearly in a reframed
    question/option-set tool call.
* It is an error if `"recommended": true` is specified on an option that is *not* the first
  option. Only the first one may be the agent's recommendation. The agent is free to be
  agnostic and not recommend anything, however.
  * If something is recommended, the text `(Recommended)` also appears in bold, just before
    the `label`: **(Recommended) JWT bearer token:** Stateless, works...
* The free-text "Other" option is never listed in `options` — it's implicit, always
  available, and rendered by the TUI, not supplied by the model. This is always rendered
  at the end of the list.
* No `multiSelect` in this iteration — each question is single-select. (Noted as
  possible future work, not built here — don't add an unused parameter for it now.)

### Result shape

```json
{
  "answers": [
    {
      "header": "Auth method",
      "question": "Which auth method should new endpoints use?",
      "answer": "JWT bearer token: stateless, works...",
    }
  ]
}
```

* `answer` is a concatenation of `${label}: ${description}` for multi-choice values, or
  is simply the user's free-text answer to the question if that's what's provided.
* If the user cancels (see "Cancel" below) partway through a multi-question call, the
  tool call fails (`error`, not `result`) rather than silently returning a partial
  `answers` list — a partial success here would be more confusing for the agent to
  interpret than a clear failure. The error text names which question was cancelled and
  echoes back any answers already collected for the earlier questions in the same call,
  so that information isn't lost, and it explicitly restates the "don't keep guessing"
  guidance (see "System prompt" below) so a model that immediately retries with a
  differently-worded version of the same question is nudged to stop and reconsider
  instead.

### Tool description

The `description()` text should mirror the "don't spin" framing from the system prompt
addition below: aimed at the agent's decision of *when* to call this tool, e.g. "Use this
when you notice you are agnostic among alternatives, guessing, second-guessing
yourself, or re-deriving the same uncertain conclusion more than once — stop and
ask the user instead of continuing to reason in circles."

## Interactive flow

Mirrors the permission-ask mechanism precisely:

1. **New exception**, defined in `ask_user_questions.py` itself (it's specific to this one
   tool, unlike `PermissionAskRequired`/`MultiPermissionAskRequired` in
   `klorb/src/klorb/permissions/table.py`, which many tools share):

   ```python
   class AskUserQuestionsRequired(Exception):
       def __init__(self, questions: list[QuestionSpec]) -> None: ...
   ```

   `AskUserQuestionsTool.apply()` validates its arguments (option count, at-most-one-
   `recommended`) and, if they're well-formed, always raises this — there is no
   "verdict" to compute here, unlike a permission check; asking *is* the tool's entire
   job. `parameters()` still rejects genuinely malformed shapes (e.g. an empty
   `options` list) before that point, the same as any other tool's argument validation.

2. **`Session` additions** in `klorb/src/klorb/session.py`, alongside the existing
   `PermissionAskContext`/`PermissionDecision`/`TurnEventHandlers.on_permission_ask`:
   * `AskUserQuestionsItemContext(BaseModel)` — one question's `header`/`question`/
     `options` (each option's `label`/`description`/`recommended`), plus `index`/`total`
     (e.g. `2`/`3`) so a UI can render "Question 2 of 3" without the caller re-deriving
     it.
   * `AskUserQuestionsAnswer(BaseModel)` — `selected_label: str | None`,
     `is_other: bool`, `other_text: str | None`, `cancelled: bool` (the Escape fast
     path — see TUI below).
   * `TurnEventHandlers.on_ask_user_questions: Callable[[AskUserQuestionsItemContext],
     AskUserQuestionsAnswer] | None = None` — new optional field, same shape and same
     "callers wire it up per-turn" contract as `on_permission_ask`.
   * `Session._run_tool_calls` (`klorb/src/klorb/session.py`, in the `try`/`except` chain
     around `tool.apply()`) gets a new `except AskUserQuestionsRequired as ask_exc:`
     branch, structurally next to the existing `MultiPermissionAskRequired`/
     `PermissionAskRequired` branches: if `callbacks.on_ask_user_questions is None` (no
     interactive plumbing — see "Headless behavior" below), fail the call with a
     clear error; otherwise call `callbacks.on_ask_user_questions` once per question, in
     order (mirroring how `_resolve_multi_permission_ask` asks about each
     `PermissionAskItem` in order), short-circuiting to a failure the moment one
     `AskUserQuestionsAnswer.cancelled` comes back `True` (see "Result shape" above for
     the error contract), otherwise assembling the full `answers` list as this call's
     `result`. Unlike the permission-ask branches, there is no "retry `tool.apply()`
     with an override" step afterward — asking the question *was* the whole operation,
     so the collected answers become the result directly.
   * Deliberately **not** gated by `SessionConfig.permission_framework` — that setting
     governs risk tolerance for resource-access verdicts (`deny`/`ask`/`auto`), which
     doesn't apply here; there is no non-interactive "auto-approve" behavior for a
     question that only the user can actually answer. An absent callback always fails
     the call (see "Headless behavior"), regardless of `permission_framework`.

3. **TUI**: new `klorb/src/klorb/tui/ask_user_questions_screen.py`,
   `AskUserQuestionsScreen(ModalScreen[AskUserQuestionsAnswer])`, one modal per question
   (matching the "one screen per item, `Session` drives the sequencing" shape
   `PermissionAskScreen` already uses for `MultiPermissionAskRequired`, rather than
   inventing a single scrollable multi-question screen). Reuses the same interaction
   idiom as `PermissionAskScreen`:
   * Header: `header` (if set) plus `question`, e.g. `"Q2/3 · Auth method"` /
     `"Which auth method should new endpoints use?"`.
   * A single-column vertical list of the question's `options`, the `recommended` one
     (if any) always sorted first regardless of the order the model supplied — this is
     the one place "recommended" has any effect; it is purely a display-order/
     visual-badge hint (e.g. an `"(recommended)"` suffix or accent color), never an
     auto-selected default. Navigated with Up/Down, confirmed with Enter — the same
     bindings `PermissionAskScreen` uses for its grid, just a single column instead of a
     2D grid since there's no independent action/scope axis here.
   * A trailing, always-present "Other..." row, identical in spirit to
     `PermissionAskScreen`'s `PERMISSION_ASK_OTHER_CELL_ID` row: selecting it reveals a
     free-text `Input` (reuse `_reveal_other_input`'s pattern) whose submission dismisses
     with `AskUserQuestionsAnswer(is_other=True, other_text=..., selected_label=None)`.
   * Escape dismisses with `AskUserQuestionsAnswer(cancelled=True, ...)` — the "Cancel"
     fast path referenced above; there is no deny/allow axis to fall back to here, so
     Escape's meaning is "stop asking me this" rather than `PermissionAskScreen`'s
     "deny once."
   * `klorb/src/klorb/tui/repl.py`: new `ReplApp._on_ask_user_questions`/
     `_confirm_ask_user_questions` pair, wired the same way as
     `_on_permission_ask`/`_confirm_permission_ask` (`call_from_thread` hop onto the
     Textual event loop, `await self.push_screen_wait(AskUserQuestionsScreen(...))`),
     and pass `on_ask_user_questions=self._on_ask_user_questions` alongside
     `on_permission_ask=self._on_permission_ask` wherever `TurnEventHandlers` is
     constructed for a turn (`klorb/src/klorb/tui/repl.py`, near line 1752).

## Headless / non-interactive behavior

The one-shot/headless prompt path (`klorb/src/klorb/session.py`, around line 1447 — the
comment there already explains it "passes no `on_permission_ask`/
`on_tool_call_limit_reached` callbacks") likewise passes no `on_ask_user_questions`. With
no user present to ask, `AskUserQuestionsRequired` always fails the tool call there,
regardless of any config — there is no sensible "auto-answer" fallback, so the agent's
own system-prompt guidance (see below) must tell it what to do in that situation: state
its assumption explicitly in its final reply, the same guidance the current "ambiguous
task" bullet already gives, rather than treat the tool as always available.

## Permission bypass

Like the scratchpad tools (`docs/adrs/scratchpad-tools-bypass-permission-tables.md`),
`AskUserQuestionsTool` gets **no** `tools.askUserQuestions.*Permission` entry in the
`deny`/`ask`/`allow` tables: those tables gate the model's access to a resource
(filesystem path, shell command) that the model could otherwise reach directly. Asking
the user a question isn't a resource access at all — there's nothing to deny or
pre-allow, only a synchronous interactive step every call to this tool inherently
requires. The interactivity itself is the gate: a headless run has no reviewer to ask,
so it fails closed unconditionally (see above), and an interactive run always shows the
prompt. Worth a short ADR alongside the scratchpad one once this is implemented, since
it's the same "no permission-table entry" call being made for a second, differently-
motivated reason (interactivity, not a fixed harness-owned location).

## System prompt

`klorb/src/klorb/resources/system_prompts.d/default_sys.md`:

* Revise the existing "Make careful, minimal changes" bullet (line ~21-22):

  > When a task is ambiguous, prefer the interpretation most consistent with the
  > surrounding code and the user's stated intent, and note the assumption you made in
  > your reply.

  to carve out when that "guess and note" default is no longer good enough — replace it
  with something like:

  > When a task is ambiguous, and the ambiguity is low-stakes or easily corrected later,
  > prefer the interpretation most consistent with the surrounding code and the user's
  > stated intent, and note the assumption you made in your reply. When the ambiguity is
  > consequential (hard to reverse, affects data or shared systems, or the possible
  > interpretations would lead to meaningfully different work), use the
  > `AskUserQuestions` tool instead of guessing — that's exactly what it's for.

* Add a new section, e.g. `## Ask instead of guessing` (near "Ground every action in the
  real workspace", which already has the "never guess at anything you can check" bullet
  this complements — that bullet is about facts you can verify yourself; this one is
  about genuine unknowns only the user can resolve):

  > * You have an `AskUserQuestions` tool: use it instead of silently picking an
  >   interpretation, inventing a plausible-sounding default, or asking the user to
  >   "let me know if this isn't what you meant" after already committing to a guess.
  > * Notice when you're spinning: if you find yourself re-deriving the same uncertain
  >   conclusion, second-guessing a choice you already made, or trying several
  >   framings of the same question to yourself without actually resolving it — that is
  >   the signal to stop and call `AskUserQuestions`, not a signal to think harder.
  >   Going in circles is a symptom of missing information, not a puzzle to be reasoned
  >   through alone.

... this is a good time to take stock of default_sys.md and do an overall edit pass. In
particular, you should use `<XmlTags>...</XmlTags>` to separate distinct sections of the
system prompt rather than ever-more-nested markdown subsection headings. XML tags add
and reinforce structure in a way that is more helpful to the agent over time.

## Specific test cases to implement

### `AskUserQuestionsTool` (`test_ask_user_questions.py`)

* `parameters()`/`apply()` reject a question with fewer than 2 or more than 4 `options`.
* `apply()` rejects a question with more than one `recommended: true` option; accepts
  zero or exactly one.
* A well-formed `questions` payload (single question, multiple questions) always raises
  `AskUserQuestionsRequired` — never returns a value directly — carrying one
  `QuestionSpec` per input question, in the same order, each field round-tripped
  faithfully (`header`, `question`, `options` with `label`/`description`/`recommended`).
* `summary()`/`detail_view()` render something sensible from `args`/`result`/`error` for
  the TUI's tool-call log (no raw JSON dump needed if a short custom summary reads
  better — follow `EditFileTool`'s precedent for a tool-specific `summary()`).

### `Session._run_tool_calls`'s `AskUserQuestionsRequired` branch

(`test_session_ask_user_questions.py`, mirroring how permission-ask branches are tested)

* `callbacks.on_ask_user_questions is None`: the tool call fails with a clear error;
  `permission_framework` value (`"ask"`/`"auto"`/`"deny"`) has no effect on this outcome.
* A multi-question call invokes `callbacks.on_ask_user_questions` once per question, one
  at a time, in order, and short-circuits on a cancellation: an answer collected for
  question 1, then `cancelled=True` on question 2, means question 3 is never asked, and
  the tool call's `error` names question 2 and echoes question 1's already-collected
  answer.
* All questions answered normally: the tool call's `result["answers"]` has one entry per
  question, in the same order, each carrying `selected_label`/`is_other`/`other_text`
  matching what the callback returned for that question.
* A `recommended` option is never auto-selected on the callback's behalf — the
  `Session`/`_run_tool_calls` layer does no default-picking; it only forwards the
  callback's `AskUserQuestionsAnswer` verbatim.

### `AskUserQuestionsScreen` (`test_ask_user_questions_screen.py`, mirroring

`test_permission_ask_screen.py` if one exists, else driven the way other Textual screen
tests in this repo are)

* The `recommended` option (if any) always renders first, regardless of its position in
  the question's `options` list.
* Up/Down navigation cycles through `options` plus the trailing "Other..." row; Enter on
  a normal option dismisses with that option's `label` as `selected_label`,
  `is_other=False`.
* Enter (or a fast-path key, mirroring `PermissionAskScreen`'s `o` binding) on "Other..."
  reveals a free-text `Input`; submitting it dismisses with `is_other=True`,
  `other_text` set, `selected_label=None`.
* Escape dismisses with `cancelled=True` from any navigation state, including from
  inside the revealed "Other" `Input`.
* `header`/`question` text and a "Question N of M" indicator render correctly for a
  multi-question call's Nth screen.

### `ReplApp._on_ask_user_questions`/`_confirm_ask_user_questions`

(`test_repl_ask_user_questions.py` or wherever `_on_permission_ask` is covered)

* The worker-thread callback hops onto the Textual event loop via `call_from_thread` and
  blocks until `AskUserQuestionsScreen` is dismissed, returning the resulting
  `AskUserQuestionsAnswer` — mirror whatever test technique already exercises
  `_on_permission_ask`/`_confirm_permission_ask`'s same `call_from_thread`/
  `push_screen_wait` hop.
* `TurnEventHandlers` built for a live turn always includes `on_ask_user_questions`
  alongside `on_permission_ask`/`on_tool_call`.

### System prompt (`test_system_prompt.py` or wherever `default_sys.md` content is

asserted)

* Assert the revised "ambiguous task" bullet's consequential-ambiguity carve-out
  language appears.
* Assert the new "Ask instead of guessing" section (or whatever its final heading is)
  appears, including the "notice when you're spinning" language.

### Registry (`test_tool_registry.py` or similar)

* `ToolRegistry(...).tools()` includes `AskUserQuestions` — confirms the single-file
  drop-in is picked up by `_discover_tools()`'s recursive walk with no further wiring.

## Specific TODO items for tasks to implement AskUserQuestions

1. **Add `klorb/src/klorb/tools/ask_user_questions.py`**: `QuestionOption`/`QuestionSpec`
   pydantic models (or plain dataclasses, matching whatever style
   `ask_user_questions.py`'s sibling tools use for structured args), the
   `AskUserQuestionsRequired` exception, and `AskUserQuestionsTool(Tool)` itself —
   `name()`, `description()` (with the "don't spin" framing), `parameters()` (2-4
   options, at-most-one-`recommended` validation), and `apply()` that validates and
   raises `AskUserQuestionsRequired` unconditionally on success.

2. **Extend `klorb/src/klorb/session.py`**: `AskUserQuestionsItemContext`,
   `AskUserQuestionsAnswer` (both `BaseModel`, next to `PermissionAskContext`/
   `PermissionDecision`), the new `TurnEventHandlers.on_ask_user_questions` field, and
   the new `except AskUserQuestionsRequired` branch in `_run_tool_calls` (item-by-item
   dispatch, short-circuit-on-cancel, no permission-framework gating, no "retry
   `tool.apply()`" step — see "Interactive flow" above for the exact contract).

3. **Add `klorb/src/klorb/tui/ask_user_questions_screen.py`**:
   `AskUserQuestionsScreen(ModalScreen[AskUserQuestionsAnswer])`, single-column
   options list with `recommended`-first ordering, trailing "Other..." row with a
   revealed `Input` (reuse `PermissionAskScreen._reveal_other_input`'s approach),
   Escape-to-cancel.

4. **Wire `klorb/src/klorb/tui/repl.py`**: `_on_ask_user_questions`/
   `_confirm_ask_user_questions` pair mirroring `_on_permission_ask`/
   `_confirm_permission_ask`; add `on_ask_user_questions=self._on_ask_user_questions` to
   every `TurnEventHandlers(...)` construction site that already sets
   `on_permission_ask`.

5. **System prompt**: revise the "ambiguous task" bullet and add the new "Ask instead of
   guessing" section to `default_sys.md`, per "System prompt" above.

6. **New ADR**: `docs/adrs/ask-user-questions-tool-bypasses-permission-tables.md` (or
   similar slug), recording the "no `tools.askUserQuestions.*Permission` entry, because
   interactivity itself is the gate" decision, alongside a cross-reference to
   `docs/adrs/scratchpad-tools-bypass-permission-tables.md` for the closest precedent and
   how this differs (harness-owned-location bypass vs. inherently-interactive bypass).

7. **New spec**: once implemented, write `docs/specs/ask-user-questions.md` describing
   the tool's argument/result shape, the interactive flow (mirroring
   `docs/specs/permissions.md`'s "Interactive 'ask' confirmation" section for the
   analogous plumbing), headless behavior, and the system-prompt guidance, as
   current-state fact — then `git mv` this plan file into `docs/plans/archive/`.
