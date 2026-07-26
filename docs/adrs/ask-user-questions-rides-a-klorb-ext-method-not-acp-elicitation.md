# `AskUserQuestions` rides `_klorb/askUserQuestions`, not ACP elicitation

* Date: 2026-07-25 19:05
* Question: `AskUserQuestions` needs a server → client round trip in the ACP world, the same
  shape `session/request_permission` already has for a permission ask. ACP's own `session/
  request_permission` doesn't fit: its `options` are a flat list of `{optionId, name, kind}`
  triples with no room for a per-option `description`, no `header`/free-text-answer concept, and
  no way to express "0 options means a plain free-text question." ACP also has an `elicitation`
  mechanism (`session/request_elicitation` in some SDK snapshots) aimed at exactly this
  "structured input from the user" shape. Should `AskUserQuestions` be adapted onto ACP
  elicitation, or given its own `_klorb/*` extension method?
* Answer: A dedicated extension method, `_klorb/askUserQuestions` (agent → client, one request
  per question in a batch, serially — see docs/specs/klorb-server.md's extension-method
  registry). `clientCapabilities._meta.klorb.askUserQuestions` gates it, mirroring
  `_klorb/raiseToolCallLimit`'s own capability-gated pattern: a client that doesn't advertise it
  gets an immediate `AskUserQuestionsAnswer(cancelled=True)`, the same fail-closed behavior a
  headless one-shot run already has, rather than a hung request.
* Reasoning: Two problems, not one.

  First, elicitation doesn't losslessly carry this tool's shape. `AskUserQuestionsItemContext`
  needs a `header` (a short chip label distinct from the full `question` text), a list of
  `QuestionOption`s each with its own optional `description`, an implicit always-available
  free-text "Other" answer even when options are offered, and an explicit `index`/`total` so a
  client can render "Question 2 of 3" without re-deriving it from a running count of its own.
  Bending elicitation's shape to fit would mean overloading fields or pushing this data into
  `_meta` on a mechanism the spec doesn't intend other implementations to have to parse. Once a
  klorb-specific `_meta` payload is doing most of the real work anyway, elicitation buys nothing
  over a purpose-built request with an honest shape.

  Second, elicitation was, at the time this decision was made, one of the newest and
  least-settled corners of the ACP spec — method name, params shape, and SDK support were all
  still moving. Building `AskUserQuestions` on top of a moving target risked a rewrite the moment
  the spec (or the pinned SDK version) changed shape underneath it, for a tool whose semantics
  (serial per-question asks, a formatted final answer string, batch-cancels-on-Escape) are
  already fully specified independent of ACP.

  The formatting rule stays server-side for the same reason `permission_decision_from_outcome`'s
  free-text redirect does: `klorb.tools.ask.common.format_answer` is the one place that turns a
  selected `QuestionOption` (or free text) into the final answer string
  (`"label"`/`"label: description"`), and the TUI's own `AskUserQuestionsPanel` already calls it
  before dismissing. Having the ACP client re-implement that formatting would risk the TUI and
  the VS Code plugin producing different answer text for the same selection — the same
  "one formatting rule stays in klorb" invariant plan-016-005 established for permission grants.

  This decision was flagged for revisit once ACP elicitation stabilizes (see the plan 016
  overview's "Decisions taken" #5) — nothing here is meant to be permanent if the protocol
  converges on a shape that actually fits.
