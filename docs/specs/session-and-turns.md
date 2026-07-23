# Session and turns

## Summary

A `Session` holds the state for klorb's active interaction with a model — its
`SessionConfig`, the `ApiProvider` it sends prompts through, and the `ModelRegistry` it
resolves model names against — and exposes the turn-based loop that both the one-shot
prompt path and the interactive REPL send their prompts through. This is framework-level:
the CLI and the REPL no longer talk to an `ApiProvider` directly, they both go through a
`Session`, so any future per-session behavior (conversation history, tool calls, persisted
config) has one place to live.

## How it works

* `klorb.session.SessionConfig` (`klorb/src/klorb/session.py`) is a `pydantic.BaseModel`
  holding the config a `Session` is constructed from:
  * `model: str` — the configured model identifier, defaulting to
    `klorb.openrouter.DEFAULT_MODEL`.
  * `interactive: bool` — whether the session stays in the REPL after its first turn,
    defaulting to `True`.
  * `thinking_enabled: bool` — whether extended-thinking is requested for turns sent to a
    thinking-capable model, defaulting to `True`.
  * `thinking_effort: ThinkingEffort` (`"low" | "medium" | "high"`) — the user-facing
    reasoning depth knob, defaulting to `"high"`. See [[default-thinking-on-and-high-effort]]
    for why these are the defaults.
  * `max_tool_calls_per_turn`/`max_tool_calls_per_session: int` — safety caps
    `Session._run_tool_calls()` enforces on individual tool-call dispatches, defaulting to
    `session.DEFAULT_MAX_TOOL_CALLS_PER_TURN`/`DEFAULT_MAX_TOOL_CALLS_PER_SESSION` (`50`/`200`).
    Unlike the other fields, `Session` mutates these itself (doubling one in place when the
    user approves continuing past it — see below), so they live on `SessionConfig` rather
    than the process-wide `ProcessConfig`; see
    [the tool-call caps ADR](../adrs/cap-tool-calls-per-turn-and-per-session.md).
    `klorb.cli.build_parser()`'s `--max-tool-calls-per-turn`/`--max-tool-calls-per-session`
    flags let a caller override either default without editing a config file.
  * `permission_framework: PermissionFramework` (`"ask" | "auto" | "deny"`) — how
    `Session._run_tool_calls()` resolves a `PermissionAskRequired` verdict, defaulting to
    `"ask"`. See docs/specs/permissions.md's "Interactive `"ask"` confirmation" section for
    the full three-way behavior. Deliberately absent from
    `klorb.process_config.SESSION_KEY_MAP`, like `interactive`: its effective default
    depends on whether the session is interactive, resolved by `klorb.cli.main()` rather
    than a static config value — see [[default-permission-framework-to-deny-headlessly]].
    Changed mid-conversation via `Session.set_permission_framework()`, not a direct field
    assignment, so `send_turn()` can tell the model about the change on the next turn — see
    docs/specs/permissions.md's "Permission framework change interjection" section.
* `klorb.session.Session` is constructed with a `SessionConfig` (saved as `self.config`),
  and optionally an `ApiProvider` (defaults to a fresh `OpenRouterApiProvider`), a
  `ModelRegistry` (defaults to a fresh `ModelRegistry()`), and a
  `klorb.tools.registry.ToolRegistry` (`tool_registry`, defaults to `None`, meaning
  tool-calling is off for this session — see [[tool-framework]] and
  [the tool-calling wiring ADR](../adrs/wire-tool-calling-into-the-session-turn-loop.md)).
  It also holds `self._messages: list[[[message-model]]]`, exposed read-only via the
  `messages` property, plus read-only `provider`/`model_registry`/`tool_registry` properties
  so callers (e.g. [[terminal-repl]]'s `/clear`) can build a new `Session` that reuses the
  same provider/registry instances (`tool_registry` is the one exception: `/clear` builds a
  fresh one, tied to the new session's config — see [[tool-framework]]).
  * `active_model_name() -> str` resolves `config.model` against the `ModelRegistry`: if
    it's a registered [[model-framework]] `Model`, its `name()` is returned (invoking the
    `Model`); otherwise `config.model` is returned unchanged, so an arbitrary OpenRouter
    model identifier still works even without a `Model` implementation.
  * `_reasoning_params() -> dict[str, Any] | None` builds the `reasoning` body passed to
    `ApiProvider.send_prompt()`: `None` if `config.thinking_enabled` is `False`, the active
    model isn't registered, or its `capabilities()["thinking"]` isn't `True`; otherwise
    `{"effort": config.thinking_effort}` or `{"max_tokens": <budget>}` depending on the
    model's `capabilities()["thinking_budget_style"]` (defaulting to `"effort"` if the
    model omits the key), with the token budget looked up from
    `THINKING_EFFORT_TOKEN_BUDGETS[config.thinking_effort]` (see
    [[map-thinking-effort-levels-to-fixed-token-budgets]]). Recomputed fresh on every turn,
    like `system_prompt`, so palette changes to thinking config take effect immediately.
  * `_drop_reasoning() -> bool` returns the active model's `Model.drop_reasoning()` (`False`
    if the active model isn't registered, matching that method's own default), passed to
    `ApiProvider.send_prompt(drop_reasoning=...)` on every round. See
    [[preserve-reasoning-across-turns-by-default]].
  * `total_tokens_used() -> int` sums every message's own `num_tokens` (see
    [[message-model]]) except a `role="thinking"` message when `_drop_reasoning()` is
    `True` — the same predicate `OpenRouterApiProvider._build_api_messages()` applies, so the
    context-usage footer always reflects exactly what would be uploaded at the start of the
    next turn, no more and no less. `total_output_tokens_used() -> int` sums `num_tokens`
    over `"assistant"`/`"tool_use"`/`"thinking"` messages unconditionally, regardless of
    `_drop_reasoning()`, since it reports everything the model has generated rather than what
    would be resent. See [[count-every-message-tokens-client-side-with-tiktoken]].
  * `send_turn(prompt: str, on_chunk: ... = None, on_thinking_chunk: ... = None,
    cancel_event: threading.Event | None = None, on_tool_call_limit_reached: Callable[[str],
    bool] | None = None) -> str` is the turn-based loop's unit of
    work. It appends a new `Message` (`role="user"`, `processing_state="pending"`) to the
    history, then sends the *entire* history plus the
    session's resolved system prompt (re-resolved fresh by `_resolve_system_prompt()` on
    every call — the model-then-default prompt with the role's own prompt, if any, layered
    on top; see [[roles-and-system-prompts]]) and `_reasoning_params()`'s result via
    `ApiProvider.send_prompt(messages, system_prompt=, model=, session_id=, reasoning=,
    on_chunk=, on_thinking_chunk=)`. As chunks arrive, `Session` (not the provider) owns the
    in-progress assistant `Message`: on the first content chunk it appends a placeholder
    (`processing_state="started_receipt"`, `streaming_content=[]`), appends each subsequent
    chunk to it, and forwards each chunk to the caller's own `on_chunk` if given (see
    [[session-owns-in-progress-assistant-message-during-streaming]]). Reasoning/thinking
    chunks are handled the same way but into a separate `role="thinking"` placeholder
    appended ahead of the assistant one (so streamed reasoning, which typically arrives
    first, and the eventual answer can be told apart), forwarded to `on_thinking_chunk`. A
    structured `reasoning_details` payload, if the provider sends one, is merged onto that
    same placeholder (lazily creating it too, if reasoning arrived as only structured details
    with no plain-text delta ever streamed) and forwarded to `on_reasoning_details` — see
    [[preserve-reasoning-across-turns-by-default]]. Every message's `num_tokens` — including
    the thinking placeholder's own, no longer zeroed out — is a client-side `tiktoken` count
    of its own content (`klorb.token_estimate.estimate_tokens`), refreshed on every streamed
    chunk; see [[count-every-message-tokens-client-side-with-tiktoken]]. On success, both
    placeholders are finalized in place (`content`, `streaming_content=None`,
    `processing_state="complete"`) — the assistant one also gets `finish_reason` from the
    response — or, for the assistant message, the provider's reply is appended fresh if no
    placeholder was ever created (e.g. a non-streaming test double; there's no non-streaming
    fallback for thinking, since without streaming there's nothing to have generated a
    thinking placeholder from). The user `Message`'s `num_tokens` is already set, from its
    own content, at construction (see below); `_dispatch_turn()` only mutates its
    `processing_state` to `"complete"` and clears `last_error` on success. On failure, it
    mutates the user `Message`
    to `processing_state="error"`/`last_error=str(exc)` (and both placeholders too, if
    created, leaving their partial `streaming_content` intact) and re-raises. If
    `cancel_event` is given and becomes set while the response is streaming in, the
    provider raises `ResponseAborted` instead of a normal failure; unlike a real error, the
    user `Message` and any placeholder(s) are kept in `self._messages` rather than removed
    or marked `"error"` — folding whatever `streaming_content` had arrived into `content`
    and setting `processing_state="aborted"` on each — since nothing actually went wrong,
    the interrupted reply may still be worth reading, and any earlier round's completed
    `tool_use`/`tool_response` messages in the same turn already ran with real side effects
    that must stay in history regardless — see
    [[escape-aborts-streaming-turn-and-discards-it-from-history]] and
    [[keep-aborted-turn-content-in-history-tagged-not-discarded]]. Both a
    one-shot prompt and every REPL submission are exactly one call to `send_turn()`. The
    streaming/placeholder mechanics above live in `Session._send_and_receive()`, one call per
    model round trip; `_dispatch_turn()` calls it in a loop when `tool_registry` is set (see
    below), so they apply to every round, not just the first.
  * The first time a turn is dispatched, `_dispatch_turn()` also inserts a `role="system"`
    `Message` at the very front of history (`_ensure_system_message()`; a no-op on later
    turns) recording the session's resolved system prompt at that point — resolution always
    produces a prompt (see [[roles-and-system-prompts]]), so one is always inserted. Like
    the `tool_defs` message below, this is bookkeeping only — it's dropped by
    `OpenRouterApiProvider._build_api_messages` and never kept in sync with a later
    `config.model` change; the live system prompt sent to the model is always the one
    re-resolved fresh by `_resolve_system_prompt()` for the *current* active model (see
    [the system-prompt bookkeeping ADR](../adrs/store-system-prompt-as-a-bookkeeping-message.md)).
  * When `tool_registry` is set and non-empty, `_dispatch_turn()` passes
    `tool_registry.tool_definitions()` as `send_prompt(tools=...)` on every round, and — the
    first time a turn is dispatched — inserts a `role="tool_defs"` `Message` right after the
    `role="system"` message, if one was inserted, or at the very front of history otherwise
    (`_ensure_tool_defs_message()`; a no-op on later turns, since it checks for one first)
    recording what was offered. That message is bookkeeping only: it's never
    sent to the model as a chat message, since `tools=` is what actually offers them (see
    [[tool-framework]] and [[message-model]]).
  * Also on the very first call to `send_turn()`, and only when the workspace is trusted, a
    `ProjectGuidance` `<SystemInterjection>` carrying the workspace's context-instruction files
    (`.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and `CLAUDE.md` when `compatibility.claudeMarkdown`
    is enabled) is prepended onto that turn's prompt, ahead of any `PermissionFramework`/
    standing interjection, before it's stored as the turn's `role="user"` `Message`
    (`_build_context_files_interjection()`; idempotent via a `self._context_files_seeded`
    flag). See [[workspace-context-files]].
  * If a round's reply requests tool calls, it's
    stored with `role="tool_use"` (instead of `"assistant"`) and `Message.tool_calls`
    populated; `_run_tool_calls()` dispatches each one via
    `tool_registry.instantiate_tool(name).apply(...)`, appending a `role="tool_response"`
    `Message` per call (rather than failing the turn if lookup or execution failed) whose
    `content` is a JSON-serialized `klorb.tools.response_envelope.ToolResponseEnvelope` — see
    "Tool-response envelope" below — and `_dispatch_turn()` sends another
    round with the updated history. This repeats until a plain `"assistant"` reply comes
    back, or one of three safety caps is exceeded and not raised past, which raises
    `ToolCallLimitExceeded` (handled like any other mid-turn failure: `user_message` marked
    `processing_state="error"`): `MAX_TOOL_CALL_ROUNDS` (200, a hard module constant, never
    raisable) model-to-tool round trips; `config.max_tool_calls_per_turn` (default
    `DEFAULT_MAX_TOOL_CALLS_PER_TURN`, 50) individual tool calls dispatched by
    `_run_tool_calls()` in this turn — `self._tool_calls_this_turn` resets to `0` at the
    start of every `_dispatch_turn()` call, including retries; or `config.max_tool_calls_per_session`
    (default `DEFAULT_MAX_TOOL_CALLS_PER_SESSION`, 200) individual tool calls dispatched
    across this `Session`'s entire lifetime — `self._tool_calls_this_session` is never reset.
    Both are checked, and the running counts logged, before each individual call within
    `_run_tool_calls()` (not just once per round), so a round requesting several parallel
    tool calls can be cut off partway through; calls already dispatched earlier in that same
    round stay in history.

    Reaching the turn or session cap isn't necessarily fatal: `_confirm_limit_increase()`
    calls `on_tool_call_limit_reached(prompt)` (the callback threaded through `send_turn()`/
    `retry_last_turn()`/`_dispatch_turn()`), if one was given, with a human-readable prompt
    describing which cap was hit. Returning `True` doubles that cap on `self.config` — for
    the rest of this `Session`'s lifetime, not just the current call — and lets the call
    proceed; returning `False`, or no callback being given at all (e.g. `klorb.cli`'s
    one-shot path), raises `ToolCallLimitExceeded` exactly as if no confirmation had been
    offered. [[terminal-repl]]'s `ToolCallLimitScreen` is what supplies this callback
    interactively. See
    [the tool-calling wiring ADR](../adrs/wire-tool-calling-into-the-session-turn-loop.md) and
    [the tool-call caps ADR](../adrs/cap-tool-calls-per-turn-and-per-session.md).
  * `retry_last_turn(on_chunk: ... = None, on_thinking_chunk: ... = None,
    on_tool_call_limit_reached: ... = None) -> str` scans
    backward for the last `role == "user"` `Message`; if it isn't in `"error"` state (or
    none exists), raises `ValueError`. Otherwise it discards everything after that message
    (e.g. a partial assistant placeholder left by a failed stream) and resends its content,
    mutating that same `Message` again rather than appending a duplicate (see
    [[mutate-the-same-message-on-turn-error-and-retry]] and
    [[session-owns-in-progress-assistant-message-during-streaming]]). Not yet wired into
    the TUI/CLI.
  * `run_one_shot(prompt: str, on_chunk: ... = None, on_thinking_chunk: ... = None) -> str`
    is a thin wrapper around `send_turn()`, named for the non-interactive entry point so
    callers don't need to know it's "just" a single turn.
* `klorb.cli.main()` (`klorb/src/klorb/cli.py`) builds a `SessionConfig` from parsed CLI
  arguments (`--model`, the resolved `--interactive` value, `--max-tool-calls-per-turn`/
  `--max-tool-calls-per-session` (each overriding the configured default when given), the
  resolved `permission_framework` (see below), and the log path returned by
  `configure_logging()`), constructs a `Session`, and either calls
  `session.run_one_shot(prompt)` and prints the result, or calls
  [[terminal-repl]]'s `run_repl(session, initial_message=prompt)` to start the REPL.
  `permission_framework` resolves to `"auto"` when `-y`/`--auto-approve` is given, else
  `"deny"` when the resolved `interactive` is `False`, else the `SessionConfig` default
  (`"ask"`) — see [[default-permission-framework-to-deny-headlessly]].
* `klorb.tui.ReplApp` (`klorb/src/klorb/tui/app.py`) takes a `Session` instead of a
  raw provider/model pair. Every submitted prompt — typed by the user or passed in as
  `initial_message` — goes through `Session.send_turn()` on a background worker thread,
  passing `on_tool_call_limit_reached=self._on_tool_call_limit_reached`. Since that runs on
  the worker thread but needs to show a modal and wait for the user's answer,
  `_on_tool_call_limit_reached()` blocks via `App.call_from_thread(self._confirm_tool_call_limit,
  message)`, where `_confirm_tool_call_limit()` is an `async def` that `await`s
  `push_screen_wait(ToolCallLimitScreen(message))` on the app's own event loop — see
  [[terminal-repl]]. Selecting a model from the command palette ([[model-framework]]) sets
  `session.config.model` directly.
* `--interactive` / `--no-interactive` (`klorb.cli.build_parser()`) is an
  `argparse.BooleanOptionalAction` defaulting to `None`. `klorb.cli.main()` resolves it to a
  bool: if the flag wasn't given explicitly, the session is interactive exactly when no
  `-m`/`--message` was given (preserving the original "no message → REPL, message → one-shot"
  default); an explicit `--interactive`/`--no-interactive` overrides that default in either
  direction. Passing `-m`/`--message` together with an explicit `--interactive` starts the
  REPL with that message auto-submitted as the first turn (see [[terminal-repl]]), so the
  user's starting message behaves exactly as if they'd typed it themselves, and the REPL
  stays open afterward. Passing `--no-interactive` without `-m`/`--message` is a usage error
  (there would be nothing to send).

## Tool-response envelope

* Every `role="tool_response"` `Message.content` `_run_tool_calls()` appends is
  `json.dumps(envelope.to_wire_dict())` for a `klorb.tools.response_envelope.ToolResponseEnvelope`
  — the harness's uniform, structured stand-in for the ad hoc `f"Error: {exc}"`-or-bare-result
  string an earlier version of this dispatch loop used. Fields: `is_error` (the sole success/
  failure discriminant), `is_retryable` (always `False` when `is_error` is `False`; otherwise
  derived from `error_category`), `error_category` (a `klorb.tools.exceptions.ErrorCategory`,
  `None` when unclassified), `error_message`, `response_body` (the tool's own result on success;
  `None` on failure except for the one deviation below), and `system_interjections`/
  `user_interjections` (below). `to_wire_dict()` omits `system_interjections`/`user_interjections`
  entirely, not just as `[]`, when empty, and omits every other `None` field, so the common
  (successful, no-interjection) case stays compact.
* `error_category` assignment: `"syntax"` for a call whose `arguments` failed to parse as JSON
  before any tool ran; `"validation"` for an unknown tool name or a caught `ValueError`;
  `"permission"` for a caught `PermissionError` or a fail-closed/denied permission ask,
  `AskUserQuestions` cancellation, or `EscalatePrivileges` denial (`AskUserQuestions`'s own
  failure modes are `"business_logic"` instead — declining to answer isn't a resource-access
  verdict); `"transient"` and any other category only via a tool explicitly raising
  `klorb.tools.exceptions.ToolCallError(msg, category=...)`; `None` for an unclassified
  exception. `klorb.tools.response_envelope.classify_exception()` is the single dispatch table
  every catch site (first-attempt or retried-after-permission-decision) routes a caught
  exception through, so categorization can't drift between call sites or go missing on a retry.
* One deviation from "`response_body` is `None` when `is_error` is `True`": a tool whose
  `apply()` never raises but signals failure through its own result shape (today, only
  `BashTool`, via its `is_success()` override for a non-zero exit/timeout/dead terminal) gets
  `ToolResponseEnvelope.error(None, category="business_logic", response_body=result)` — `
  error_message` stays `None` (the detail already lives in `response_body["failure_reason"]`) but
  `response_body` still carries the tool's own result, so a failed shell command's `stdout`/
  `stderr` aren't discarded. See
  [the structured tool-response envelope ADR](../adrs/wrap-tool-responses-in-a-structured-json-envelope.md).
* `system_interjections` carries the same kind of harness advisory as an XML
  `<SystemInterjection subject="...">` block prepended to a user-turn prompt (see
  `_wrap_system_interjection()` above), just delivered a second way: `_run_tool_calls()` polls
  every `_standing_interjection_providers` entry once per call to itself (i.e. once per
  tool-call round, not once per individual call), and attaches whichever providers returned a
  message to the first envelope built in that round. This keeps a standing reminder (e.g.
  `TodoNextTool`'s current-task nudge) visible even deep inside a multi-round tool loop that
  never returns to a fresh user-turn prompt — the exact case where the XML-on-user-prompt
  delivery alone would go stale.
* `user_interjections` carries user messages queued during an active agent turn. When the
  user presses Enter while a turn is in flight, the TUI queues the message (via
  `Session.enqueue_queued_message`) and mounts a `<Queued message>` header plus the
  message text in italics in the history. The next time `_run_tool_calls()` runs (i.e. the
  next tool-call round), it drains the queue (via `Session.drain_queued_messages`) and
  attaches each message as a `UserInterjectionPayload` on the first envelope built in that
  round, mirroring how `system_interjections` are delivered. When the turn finishes, the
  TUI transitions the history widgets from italics to regular styling to confirm delivery.

## Session naming

* `klorb.session_naming` derives a short human title and a kebab-case id slug for a fresh
  session from its first submitted prompt, via a cheap/fast model — mirroring
  `klorb.permissions.risk_classifier`'s structured-output/`e2e_timeout`/one-retry pattern for
  an unrelated small-model classification task. The classifier runs inside `Session.send_turn()`
  itself (`SessionCoreMixin._run_session_naming`, in `klorb.session.mixins.core`), as one of the
  one-shot blocks `send_turn()` runs on the first call to a given `Session` instance (alongside
  the skills/context-files/metadata interjections) — so it fires identically whether that first
  call comes from the interactive TUI or a headless one-shot `-m`/`--message` invocation
  (`Session.run_one_shot`).
* `generate_session_name(prompt_text, *, api_provider, model, timeout, e2e_timeout, reasoning=None)
  -> SessionName | None` sends one request asking for a `title` (a short plain-English summary)
  and a `slug` (kebab-case, at most 4 words, validated by a pydantic `field_validator`).
  Returns `None` on any failure — a request error, `timeout`/`e2e_timeout` exceeded, or a reply
  that still fails to parse/validate after one retry — never raises. `default_naming_model`
  picks a model the same way `klorb.permissions.risk_classifier._default_classifier_model`
  does: the first registered model whose `Model.klorb_capabilities()` reports `"NANO_CLASSIFIER"`
  (`ModelRegistry.find_by_capability`), falling back to `DEFAULT_SESSION_CLASSIFIER_MODEL`
  (`"openai/gpt-5-nano"`, which declares that capability in its packaged
  `resources/models/gpt-5-nano.json`). `thinking_effort_for` requests `{"effort": "low"}`
  when the resolved model is thinking-capable, so this one-shot summarization task doesn't
  inherit a costlier provider-side reasoning default. When a `Session` has no `ProcessConfig`
  (most unit tests), `_run_session_naming` falls back to `DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS`/
  `_E2E_TIMEOUT_SECONDS` and `default_naming_model(self)` for the timeouts/model.
* On success, `_run_session_naming` calls `rename_session_id(old_id, slug)` to replace the
  random-nonce suffix of `Session.id` (assigned at construction by `generate_session_id()`, see
  "Filesystem paths and logging" below) with the derived `slug`, keeping the `yyyy-mm-dd-hh-mm`
  timestamp prefix — e.g. `2026-07-19-14-30-happy-otter` becomes `2026-07-19-14-30-fix-auth-bug`
  — and applies it via `Session.set_id()`, which also renames `Session.root_id` to match
  whenever `id`/`root_id` hadn't already diverged (see [[chainlink-task-tracking]] for why
  `root_id`, not `id`, is what `get_chainlink_label()` returns — a renamed `id` with a
  stale `root_id` would otherwise leave every chainlink issue created in the session labeled
  with its original random nonce forever). `Session.name` is also set to the derived title.
* `send_turn()` invokes `TurnEventHandlers.on_session_name_changed`, if given, exactly once —
  with the derived `SessionName` on success, or `None` on failure — right after applying (or
  failing to apply) the rename, so a caller can react. The TUI's reaction
  (`PromptSubmissionMixin._handle_session_name_changed`) renames the session's already-open log
  file to match on success, when session logging is enabled (see [[paths-and-logging]]), and
  updates the `SESSION_NAME_ID` status line (`"Session: <title>"`, between the prompt input and
  the footer) to the derived title; on failure it shows the session's own random nonce
  (`session_id_suffix(session.id)`) as its "title" instead, so the line always reflects the
  actual session id rather than getting stuck on a generic placeholder. A headless one-shot
  invocation passes no `on_session_name_changed`, so its `Session` still gets renamed but
  nothing reacts to it, and its session log file (if any) keeps its original name — log-file
  management is TUI-only regardless of naming (see [[paths-and-logging]]).
* Naming is attempted at most once per `Session` instance, tracked by `Session.
  session_naming_pending` (a read-only view of `_session_naming_pending`, seeded `False` when a
  `session_name` was already supplied to the constructor — a restored, already-named session —
  and `True` otherwise; set `False` unconditionally the moment `send_turn()`'s naming block runs,
  before it even knows whether naming will succeed) — so a failed/timed-out attempt is never
  retried on a later turn within the same `Session`, and a fresh `Session` (a new session, or
  `/clear`) always starts with naming pending again since it's constructed without a
  `session_name`. Restoring the last saved session (see [[session-persistence]]) passes the
  saved `Session.name` straight into the constructor, carrying it forward instead of
  re-triggering the classifier; the TUI's status line is set to match right after construction:
  `"Session: <title>"` when the save file has one, or `"New session..."` when it doesn't (an
  older save file predating `Session.name`, or a session whose first-prompt naming never
  completed) — in the latter case `session_naming_pending` is `True`, so the classifier still
  runs on the next prompt submitted to the restored session.
* `classifier.model` / `classifier.timeout` / `classifier.e2eTimeout` (`PROCESS_KEY_MAP`, top
  level — see [[process-and-session-config]]) configure
  `ProcessConfig.session_classifier_model`/`_timeout_seconds`/`_e2e_timeout_seconds`, defaulting
  to unset/`5.0`/`10.0`. Named generically (`classifier`, not `sessionNamer`) since this same
  small/cheap classifier model choice may be reused for other structured-output tasks beyond
  session naming.
* While the classifier call is in flight, the TUI's history shows an animated "Getting ready..."
  notice (`klorb.tui.widgets.tool_call_widgets.GettingReadyStatic`) — the same crawling
  bright-white-character animation `RunningToolCallStatic` uses for "Running...", via the
  shared `klorb.tui.formatting.crawl_animation_text` helper. `PromptSubmissionMixin._send_prompt`
  mounts it before calling `Session.send_turn()` (only when `session_naming_pending` is still
  `True`) and unmounts it from within the `on_session_name_changed` reaction, before mounting
  `TurnWaitingStatic` for the turn itself — so the two "still working" notices never overlap.

## Usage

```bash
klorb -m "What is 2+2?"                  # one-shot: Session.run_one_shot, no REPL
klorb -m "What is 2+2?" --interactive    # REPL, with the message as the first turn
klorb --no-interactive                   # error: nothing to send
klorb                                    # REPL, no initial message (default)
```

## Out of scope

* `retry_last_turn()` exists on `Session` but isn't yet exposed through the TUI or CLI.
* Persisting `SessionConfig` across invocations is not implemented yet.
* `retry_last_turn()` restarts a tool-calling turn from scratch (resending the original user
  message) rather than resuming mid-round-trip: it discards everything after the last user
  message, including any `tool_use`/`tool_response` messages from earlier rounds of the
  failed attempt.
* `thinking_enabled`/`thinking_effort` have no `--thinking`/`--thinking-effort` CLI flags
  yet; they're only reachable via [[terminal-repl]]'s command palette, so a one-shot
  `klorb -m ...` invocation always uses the `SessionConfig` defaults (thinking on, `"high"`
  effort).
