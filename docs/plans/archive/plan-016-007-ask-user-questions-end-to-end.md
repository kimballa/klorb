# Plan 016, increment 007: AskUserQuestions end to end

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

The `AskUserQuestions` tool works from the VS Code client: the server forwards each
question through a new `_klorb/askUserQuestions` extension request (agent → client), and
the webview renders the TUI's questions panel — option list, free-text answer,
"Question 2 of 3" progression, Escape-cancels-the-batch semantics. One increment covers
both sides because each side alone is small and untestable end to end.

Why an extension method rather than ACP elicitation: recorded in the plan overview
("Decisions taken" #5) — the multi-question batch with headers, described options, and
free-text answers doesn't survive the current elicitation shape, which is also among the
least-settled parts of the protocol. Write the ADR
(`docs/adrs/ask-user-questions-rides-a-klorb-ext-method-not-acp-elicitation.md`) as part
of this increment.

## Server deliverables (python)

* `TurnBridge` registers `on_ask_user_questions`; each
  `AskUserQuestionsItemContext` becomes one blocking `_klorb/askUserQuestions` ext
  request with params:
  `{sessionId, header, question, options: [{label, description?}], index, total}`
  (index/total verbatim from the context — the server asks serially, one request per
  question, exactly as the TUI panel is driven). Result:
  `{selectedOptionIndex: int} | {otherText: string} | {cancelled: true}`. The server —
  not the client — formats a selected option into the final answer string (`"label"` /
  `"label: description"`, via `klorb.tools.ask.common.format_answer`) before building the
  `AskUserQuestionsAnswer`, so the one formatting rule stays in klorb (mirror of the
  grant-pattern invariant in 005).
* Call only when `clientCapabilities._meta.klorb.askUserQuestions` is advertised; when
  not, return `AskUserQuestionsAnswer(cancelled=True)` — the tool reports the batch as
  declined, same as the TUI's Escape, instead of hanging a headless client.
* Spec: extension-method registry entry with both param/result shapes.

## Client deliverables (typescript)

* Advertise `clientCapabilities._meta.klorb.askUserQuestions = true`. Handle the ext
  method like `requestPermission` in 006: promise held host-side, `questionAsk` /
  `questionAnswer` webview messages (extend the union + history model; reuse the
  pending-state persistence and re-post machinery built in 006 — generalize that slot to
  "pending interaction" covering both asks rather than duplicating it).
* `QuestionPanel` (`src/webview/components/QuestionPanel.tsx`), mounted in the
  interaction area: header chip, question text, "Question N of M" caption,
  `<vscode-radio-group>`-style option list (label bold, description dim) plus an
  "Other…" free-text row; Submit posts the selected index or other-text; Escape posts
  `cancelled` (the whole batch stops server-side — note this in the panel's caption UX
  copy: "Esc dismisses remaining questions").
* Decision recorded to history as an `interaction` entry (question + chosen answer),
  per 006's pattern.

## Tests

* Python (`test_acp_server_ask_user_questions.py`, harness): a scripted
  `AskUserQuestions` tool call — three questions; harness answers option / other-text /
  cancelled across parametrized runs; assert the tool's returned answer strings apply
  `format_answer` correctly and cancellation short-circuits the batch (remaining
  questions never asked — assert request count). Capability-absent client → immediate
  cancelled answer, no ext traffic.
* TypeScript: `klorbAcpClient.test.ts` — ext request → message → each answer shape maps
  back correctly; `QuestionPanel.test.tsx` — option select, other-text, Escape, N-of-M
  rendering; history-model interaction record.

## Checkpoint criteria

* Both subprojects green.
* Manual: prompt the agent to "ask me three questions about X before proceeding";
  answer one by option, one by free text; cancel the third and observe the batch stop.
* Specs updated on both sides.
