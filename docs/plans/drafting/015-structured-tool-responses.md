# PLAN-015: Structured tool-response envelope with uniform error categorization

Addresses the TODO.md "Feature backlog" bullet: "Add a structured wrapper around all tool call
responses. In particular, standardized mechanisms for error reporting" (and its "Tool call
responses are also a good opportunity for system interjections" sub-bullet).

## Problem

`SessionToolExecutionMixin._run_tool_calls` (`klorb/src/klorb/session/mixins/tool_execution.py`)
turns each tool call's `(result, error)` pair into the `tool_response` `Message.content` string
via `_format_tool_response_content`:

```python
def _format_tool_response_content(result: Any, error: str | None) -> str:
    if error is not None:
        return f"Error: {error}"
    return result if isinstance(result, str) else json.dumps(result)
```

This has three problems this plan fixes together, since they all touch the same choke point:

1. **No error semantics for the model to reason about.** `"Error: {msg}"` is a flat string --
   the model can't tell a malformed-JSON-arguments mistake (fix the call and retry) from a
   permission denial (retrying won't help) from a transient network hiccup (retrying might
   help) without guessing from prose. Anthropic's own MCP tool-design guidance recommends a
   small structured field set for exactly this (`is_error`/`is_retryable`/`error_category`/
   `error_message`) -- see
   <https://claudecertificationguide.com/learn/2-tool-design-mcp/2-2-structured-error-responses>.

2. **Tools signal "this call didn't really work" three incompatible ways today**, none of which
   `_format_tool_response_content` knows about:
   * Raise an exception (`ValueError`, `PermissionError`, a permission-ask exception, ...) --
     `error` gets set, `result` stays `None`.
   * Return a result dict carrying its own ad hoc failure fields and never raise --
     `BashTool.apply()` (`klorb/src/klorb/tools/bash.py`) returns `{"success": bool,
     "failure_reason": str | None, "stdout": ..., "stderr": ..., ...}` unconditionally, even for
     a non-zero exit, a timeout, or a dead persistent shell; `Tool.is_success()`'s default
     `error is None` heuristic can't see any of that, which is why `BashTool` is the only tool
     in the codebase overriding `is_success()` (`klorb/src/klorb/tools/bash.py:1253`).
   * Return a result dict with a bare `"error"` key and never raise -- `WebFetchTool.apply()`
     (`klorb/src/klorb/tools/web/fetch.py`) does this for an unsupported HTTP method, a domain
     parse failure, a request timeout, a connection error, and a missing-`Session` spill
     failure (lines 174, 181, 203, 221, 225, 233, 282, 319, 345). `WebFetchTool` has no
     `is_success()` override, so none of these are visible to `Tool.is_success()` /
     `SessionStatistics.tools` today -- a real latent gap, not just a style inconsistency.

3. **Standing interjections go stale mid-turn.** `Session.register_standing_interjection()`
   (`klorb/src/klorb/session/mixins/core.py:389`) polls every registered provider once per
   top-level `send_turn()` call and prepends the result onto the *next user prompt* as an XML
   `<SystemInterjection>` block (`klorb/src/klorb/session/mixins/turns.py:69`,
   `_wrap_system_interjection`). But a single `send_turn()` can drive many
   `_run_tool_calls`/`_send_and_receive` round trips before the model finally stops asking for
   tools (`SessionTurnsMixin._dispatch_turn`'s `while reply.role == "tool_use"` loop). A standing
   reminder like `TodoNextTool`'s "your current tracked task is #123" (`klorb/src/klorb/tools/
   tasks/todo_next.py:23-50`) is invisible for the entire rest of that turn once the first prompt
   scrolls out of recent context -- exactly the deep-tool-loop case where the model most needs
   reminding.

4. **No reserved slot for delivering queued user messages mid-tool-loop.** Not built in this
   plan (see "Out of scope" below), but the wire schema should have a field for it now so a
   later plan doesn't need another wire-format migration.

## Solution overview

* A new `klorb.tools.response_envelope` module defines `ToolResponseEnvelope` (a frozen pydantic
  `BaseModel`) -- the JSON object that becomes every `tool_response` message's `content`, in
  place of today's bare-result-or-`"Error: ..."` string.
* `klorb.tools.exceptions` (already the shared-tools-exceptions module, currently just
  `NoSuchToolException`) gains `ErrorCategory` (a `Literal` of the five categories) and
  `ToolCallError`, a general-purpose exception any tool can raise to signal a categorized,
  optionally payload-carrying failure without inventing its own ad hoc result-dict shape.
* `_run_tool_calls` classifies every failure -- caught exception, or a resolved-but-denied ask --
  into `(error_message, category, response_body)` via one shared classification path, then builds
  one `ToolResponseEnvelope` per call and serializes *that* as `content`, instead of the raw
  result/error.
* The existing `_standing_interjection_providers` registry (unchanged) is polled once per
  `_run_tool_calls` call (i.e. once per tool-call round, not once per individual call) and
  attached, JSON-formatted, to the first envelope in that round only.
* `user_interjections` is reserved on the schema now (always an empty list), populated by a later
  plan.
* `WebFetchTool`'s ad hoc `"error"` result key is retired in favor of raising (`ValueError` /
  `ToolCallError`), so the envelope's `is_error`/`error_message` become the single source of
  truth there, matching what `error_category`/`is_retryable` are for. **Locked in** -- see
  "Changes to existing files" below.
* `BashTool` keeps its existing non-raising, rich-result-dict design for a failed command
  (see "Design decision: Bash exit status" below) -- this is the one deliberate deviation from
  "the envelope's `response_body` is `None` whenever `is_error` is `True`". **Locked in**: this
  plan implements the `is_success()`-driven `business_logic` categorization described there, not
  a literal `BashTool.apply()` raise.

## New module: `klorb/src/klorb/tools/response_envelope.py`

```python
from klorb.tools.exceptions import ErrorCategory, NoSuchToolException, ToolCallError

_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset({"transient", "syntax", "validation"})

class SystemInterjectionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    subject: str
    body: str

class UserInterjectionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    user_message: str

class ToolResponseEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)
    is_error: bool
    is_retryable: bool
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    response_body: Any = None
    system_interjections: list[SystemInterjectionPayload] = Field(default_factory=list)
    user_interjections: list[UserInterjectionPayload] = Field(default_factory=list)

    @classmethod
    def success(
        cls, response_body: Any, *, system_interjections: list[SystemInterjectionPayload] = (),
    ) -> "ToolResponseEnvelope": ...
        # is_error=False, is_retryable=False unconditionally (an empty Grep match list is not
        # something to retry -- see Problem #1).

    @classmethod
    def error(
        cls, message: str, *, category: ErrorCategory | None, response_body: Any = None,
        system_interjections: list[SystemInterjectionPayload] = (),
    ) -> "ToolResponseEnvelope": ...
        # is_error=True; is_retryable = category in _RETRYABLE_CATEGORIES (False when category
        # is None -- an unclassified failure defaults to "don't retry blindly").

    def to_wire_dict(self) -> dict[str, Any]:
        ...
        # model_dump(exclude_none=True), then additionally drops system_interjections/
        # user_interjections entirely (not just `[]`) when empty -- every tool_response pays
        # this envelope's overhead now, so the dominant (no-interjection) case should not carry
        # two empty-array keys on every single call.


def classify_exception(exc: Exception) -> tuple[str, ErrorCategory | None, Any]:
    """Return (message, category, response_body) for a tool-call exception, shared by every
    except-Exception site in `klorb.session.mixins.tool_execution` and `...mixins.permissions`
    (including retried-call exceptions) so categorization is never duplicated or missed on a
    retry path.
    """
    if isinstance(exc, ToolCallError):
        return str(exc), exc.category, exc.response_body
    if isinstance(exc, PermissionError):
        return str(exc), "permission", None
    if isinstance(exc, (ValueError, NoSuchToolException)):
        return str(exc), "validation", None
    return str(exc), None, None
```

`PermissionError` classification covers both `klorb.permissions.table.raise_if_not_allowed`'s
explicit `raise PermissionError(f"Permission denied: {resource_description}")` for a `"deny"`
verdict, *and* a real OS-level access-denied failure -- Python already maps `errno=EACCES` and
Windows `winerror=5` uniformly onto the builtin `PermissionError` (a subclass of `OSError`), so no
platform-specific errno/winerror inspection is needed; `isinstance(exc, PermissionError)` already
covers both origins identically. `NoSuchToolException` is folded into the same `validation` branch
as `ValueError` here for `classify_exception`'s benefit (retried calls that hit it), even though
`_run_tool_calls`'s primary dispatch keeps its own dedicated `except NoSuchToolException` branch
(see below) rather than routing through `classify_exception` for that first-attempt case.

### `ToolCallError`, added to `klorb/src/klorb/tools/exceptions.py`

```python
ErrorCategory = Literal["transient", "syntax", "validation", "permission", "business_logic"]

class ToolCallError(Exception):
    """Raise from any `Tool.apply()` to signal a categorized failure without inventing a
    tool-specific result-dict failure shape (see `klorb.tools.response_envelope`).
    `response_body`, if given, becomes the failed call's `ToolResponseEnvelope.response_body`
    instead of `None` -- for a tool whose failure carries data worth keeping (partial output,
    diagnostic detail) even though the call as a whole didn't succeed.
    """

    def __init__(
        self, message: str, *, category: ErrorCategory = "business_logic",
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.response_body = response_body
```

`ErrorCategory` and `ToolCallError` live in `exceptions.py` (not `response_envelope.py`) so that
module keeps its existing role -- a dependency-light leaf every tool can import without pulling in
pydantic-model machinery -- and so `response_envelope.py` can depend on `exceptions.py` in one
direction only (`classify_exception` needs `ToolCallError`/`NoSuchToolException`); the reverse
would cycle.

## Error-category assignment table

| Site | Condition | `error_category` | `response_body` |
|---|---|---|---|
| `_run_tool_calls`, before any tool runs | `call.arguments` fails `json.loads` | `"syntax"` | `None` |
| `_run_tool_calls` | `NoSuchToolException` (unknown tool name) | `"validation"` | `None` |
| Any `except Exception` site (first attempt or retry) | `isinstance(exc, ValueError)` | `"validation"` | `None` |
| Any `except Exception` site | `isinstance(exc, PermissionError)` | `"permission"` | `None` |
| Any `except Exception` site | `isinstance(exc, ToolCallError)` | `exc.category` | `exc.response_body` |
| Any `except Exception` site | anything else (unclassified) | `None` | `None` |
| `PermissionAskRequired`/`MultiPermissionAskRequired` | fails closed (`permission_framework="deny"`, no `on_permission_ask` callback, a non-persistable resource, or the user answers deny/`other_text`) | `"permission"` | `None` |
| `EscalatePrivilegesRequired` | denied, or no `on_escalate_privileges` callback | `"permission"` | `None` |
| `AskUserQuestionsRequired` | cancelled, or no `on_ask_user_questions` callback | `"business_logic"` | `None` |
| `apply()` returns normally, `Tool.is_success(args, result, None)` is `False` | (`BashTool` today) | `"business_logic"` | **the tool's own `result`** -- see below |
| `apply()` returns normally, `Tool.is_success(...)` is `True` | -- | *(`is_error=False`)* | the tool's own `result` |

`is_retryable` is never set directly by a caller -- it's always derived from `error_category` via
`_RETRYABLE_CATEGORIES = {"transient", "syntax", "validation"}`, `False` for `"permission"`/
`"business_logic"`/unclassified (`None`), and `False` unconditionally when `is_error` is `False`
(so an empty, correctly-executed `Grep` doesn't read as something to retry).

No klorb call site raises `"transient"` today -- there's no built-in Python exception that
uniquely means "retryable network hiccup" the way `PermissionError` does for permission. It
exists in the enum for a tool to opt into explicitly via `ToolCallError(msg,
category="transient")`; `WebFetchTool`'s timeout/connection-error/HTTP-429 cleanup below is the
first (and, for this plan, only) user of it.

## Design decision: Bash exit-status handling (locked in)

The user-facing request for this plan says "Bash tool exit status non zero should be a more
generic `ToolCallError` that routes to the category of business_logic." Implemented literally --
`_execute`/`_execute_persistent` (`klorb/src/klorb/tools/bash.py:898-1154`) raising
`ToolCallError` instead of returning their result dict on `success=False` -- this would:

* Make `result` `None` on a failed command (per the existing "when `error` is set, `result` is
  meaningless" contract every `Tool.summary()`/`detail_view()`/`diff_preview()`/`read_preview()`
  override relies on), discarding `stdout`/`stderr`/`exit_status`/`runtime` unless they're
  packed into `ToolCallError.response_body` instead -- workable, but it means rewriting
  `BashTool.summary()` (`bash.py:1264`, keys off `result.get("success")`/`result.get("exit_status")`)
  and `detail_view()` (`bash.py:1278`) to read from the exception's payload for a failed call and
  `result` for a successful one -- two different data sources for the same rendering logic.
* Remove the one existing `is_success()` override entirely, since a raised exception already
  means `error is not None` under the base class's default.
* Touch `_execute`, `_execute_persistent`, and `_sandbox_stale_response` (`bash.py:1191-1200`,
  today already returning a result dict with `success=False` for the reconcile-on-grow refusal
  case) -- three separate non-zero/failure result-construction sites, each of which would need to
  become a raise instead of a return.

This plan instead keeps `BashTool.apply()`'s existing contract (never raises for a command that
merely ran and failed; keeps signaling failure via its own `"success"`/`"failure_reason"` result
fields, unchanged) and does the categorization one layer up: `_run_tool_calls` already calls
`tool.is_success(args, result, error)` once per call, purely for `SessionStatistics.tools`
bookkeeping (`klorb/src/klorb/session/mixins/tool_execution.py:211-217`). This plan adds one more
read of that same, already-computed value: when `error is None` but `is_success(...)` is `False`,
the envelope is still built as `ToolResponseEnvelope.error(category="business_logic",
response_body=result)` -- `is_error=True`, but with `response_body` populated from the tool's own
result rather than dropped, and `error_message` left `None` (the failure detail already lives in
`response_body["failure_reason"]`; duplicating it into `error_message` would violate this same
plan's own "don't duplicate wrapper fields" principle in the other direction).

This satisfies the request's actual intent -- a failed shell command is now visibly
`is_error=true`/`error_category="business_logic"`/`is_retryable=false` to the model, exactly as
asked -- without rewriting `BashTool`'s internals, its `summary()`/`detail_view()`/`is_success()`
overrides, or its three result-construction call sites, and without losing `stdout`/`stderr` on a
failed command (arguably the single most useful part of a failed command's response). The
tradeoff is that `response_body` is no longer unconditionally `None` when `is_error` is `True` --
this is the one place in the envelope's contract with an exception, and it's why this section
exists as its own callout rather than being folded silently into the assignment table above.

**Decision: accepted as written above.** `BashTool.apply()`'s internals, `summary()`,
`detail_view()`, and `is_success()` are unchanged; the `business_logic` categorization is applied
one layer up in `_run_tool_calls`, exactly as described. Nothing in `bash.py` itself changes for
this plan.

## Changes to existing files

**`klorb/src/klorb/tools/exceptions.py`**
* Add `ErrorCategory` (`Literal["transient", "syntax", "validation", "permission",
  "business_logic"]`) and `ToolCallError` (above), alongside the existing `NoSuchToolException`.

**`klorb/src/klorb/tools/response_envelope.py`** (new)
* `SystemInterjectionPayload`, `UserInterjectionPayload`, `ToolResponseEnvelope`,
  `classify_exception()` (all above).

**`klorb/src/klorb/session/events.py`**
* New `ToolCallOutcome` (small `@dataclass`, not a pydantic model -- it's an internal
  `_run_tool_calls`-loop value, never serialized): `result: Any = None`, `error: str | None =
  None`, `category: ErrorCategory | None = None`, `response_body: Any = None`. Replaces the
  bare `tuple[Any, str | None]` every `_resolve_*`/`_retry_after_*` permissions-mixin method
  returns today -- see `.claude/skills/encapsulate-in-classes/SKILL.md`'s guidance against
  returning loosely-related values positionally. `result`/`response_body` are mutually
  exclusive by convention (mirroring `ToolResponseEnvelope`): `result` is meaningful only when
  `error is None`, `response_body` only when it's not.
* `ToolCallEvent` (the UI-facing event) is unchanged -- it keeps taking plain `result: Any`/
  `error: str | None`, derived from `ToolCallOutcome.result`/`.error` exactly as `_run_tool_calls`
  derives them today, so `Tool.summary()`/`detail_view()`/`is_success()` and every UI consumer of
  `ToolCallEvent` need no changes. Only the wire `content` string changes shape.

**`klorb/src/klorb/session/mixins/permissions.py`**
* `_retry_after_permission_decision`, `_retry_after_multi_permission_decisions`,
  `_resolve_multi_permission_ask`, `_resolve_ask_user_questions`, `_resolve_escalate_privileges`:
  return `ToolCallOutcome` instead of `tuple[Any, str | None]`. Every existing `return None,
  f"Permission denied: ..."` becomes `return ToolCallOutcome(error=..., category="permission")`;
  the `AskUserQuestions` cancelled/no-callback returns become `category="business_logic"`; the
  `EscalatePrivileges` denied/no-callback returns become `category="permission"`. Every `except
  Exception as exc: return None, str(exc)` retry-failure branch (`_retry_after_permission_decision`,
  `_retry_after_multi_permission_decisions`) becomes `message, category, response_body =
  classify_exception(exc); return ToolCallOutcome(error=message, category=category,
  response_body=response_body)` -- so a retried call's own `ValueError`/`PermissionError`/
  `ToolCallError` is categorized exactly like a first-attempt one, not silently downgraded to
  unclassified.

**`klorb/src/klorb/session/mixins/tool_execution.py`**
* Poll `_standing_interjection_providers` once at the top of `_run_tool_calls` (before the `for
  call in tool_use_message.tool_calls:` loop), building `list[SystemInterjectionPayload]` the
  same sorted-by-subject order `send_turn()` already uses -- reusing the existing registry
  unchanged, not a second one.
* Every branch that today sets a bare `error = str(exc)` (the `PermissionAskRequired`
  fail-closed branches, the generic `except Exception as exc` catch-all, the `NoSuchToolException`
  branch) instead threads a `category` (and, for the generic catch-all, a `response_body` from
  `classify_exception`) alongside it. Branches that call into a `_resolve_*` mixin method unpack
  the returned `ToolCallOutcome` instead of a tuple.
* Replace `_format_tool_response_content(result, error)` with a new step, run once per call after
  the existing `tool_stats` bookkeeping block (`tool_execution.py:211-217`, itself unchanged): if
  `error is None and tool is not None and not tool.is_success(args, result, error)`, treat this
  as the Bash-style deviation (`ToolResponseEnvelope.error(category="business_logic",
  response_body=result)`); elif `error is None`, `ToolResponseEnvelope.success(result)`; else
  `ToolResponseEnvelope.error(message=error, category=category, response_body=response_body)`.
  Attach `system_interjections` only when `call is tool_use_message.tool_calls[0]`. `content =
  json.dumps(envelope.to_wire_dict())`.
* `_format_tool_response_content` itself is deleted (its whole job moves into the step above).

**`klorb/src/klorb/tools/web/fetch.py`** -- **locked in**

* Remove the `{"error": ...}` result-dict pattern (lines 174, 181, 203, 221, 225, 233, 282, 319,
  345 -- see Problem #2) in favor of raising:
  * Unsupported `method` (line 172-175): `raise ValueError(...)` (`validation`).
  * `parse_domain` failure (line 178-181): let the existing `ValueError` from `parse_domain`
    propagate instead of catching and re-wrapping it into a dict (`validation`).
  * `httpx.TimeoutException`/`httpx.RequestError` (lines 219-226): `raise ToolCallError(...,
    category="transient")`.
  * **HTTP 429 from the fetched server** (new check, right after `response_code = response.
    status_code` at line 237, before the body is read): `if response_code == 429: raise
    ToolCallError(f"Fetch {url} was rate-limited (429).", category="transient")` -- a 429 is the
    canonical retryable-later case and shouldn't be handed back as an ordinary `response_code`
    inside a successful `response_body` the way a 200/404/500 is.
  * **HTTP 401/403 from the fetched server** (same new check, alongside the 429 one): `if
    response_code in (401, 403): raise ToolCallError(f"Fetch {url} was rejected ({response_code}
    {response_text}).", category="permission")` -- the remote server's own auth/authorization
    rejection, distinct from (and checked after) klorb's own domain-permission-ask
    (`raise_if_not_allowed(..., url=url)` at line 191-195, which governs whether klorb will fetch
    the domain at all, before any request is sent). Not retryable, same as any other `permission`
    result.
  * Any other status code (200, other 4xx/5xx besides 401/403/429) is left exactly as today -- a
    normal, successful `response_body` carrying `response_code`, since the fetch itself worked and
    the model needs to see the actual status to decide what to do next.
  * "No session available for spill" (lines 319, 345): `raise ToolCallError(..., 
    category="business_logic")` -- not a bad argument, a missing-infrastructure precondition.
  * The three `user_cancel`/`body_exceeded_max_bytes` dicts (lines 203, 233, 282, 309) are **not**
    errors -- a cancelled or truncated-but-returned fetch is a legitimate, successful outcome
    (mirrors `BashTool`'s own cancellation handling, which also isn't an error). These stay
    exactly as they are, `response_body` on the success path, but their key changes from `"error"`
    to `"message"` (or is dropped where `"incomplete_reason"` already says the same thing) so a
    reader doesn't mistake an `is_error=false` envelope's `response_body` for carrying a literal
    `"error"` field.

**`klorb/src/klorb/resources/system_prompts.d/default_sys.md`**
* Extend the "Continuing system context" section (currently only documents the
  `<SystemInterjection>` XML wrapper used in user-turn prompts, around line 281-293) with a new
  paragraph documenting the `tool_response` JSON envelope shape: `is_error`/`is_retryable`/
  `error_category`/`error_message`/`response_body`, and that `system_interjections` inside it
  carry the same kind of harness advisory as an XML `SystemInterjection` block, just delivered
  differently because it rides along with a tool result instead of a user turn. Document
  `user_interjections`'s *concept* here too (per the request: "treat it with the same importance
  as a regular user turn"), even though nothing populates it yet -- so the wire schema and its
  prompt documentation land together, and the follow-up plan that populates it doesn't also need
  a prompt-doc change.

## Tests to update

`_format_tool_response_content`'s output shape is asserted on, directly or via
`tool_response_message.content`, throughout `klorb/tests/klorb/session/test_session.py` (e.g.
lines 1155, 1275, 1314-1315, 1332-1334, 1774-1776 and others matching `.content ==`/`.content
.startswith("Error:")`/`.content.endswith(...)` against a tool_response message) --
essentially every one of these needs to parse the JSON envelope and assert against
`response_body`/`is_error`/`error_category` instead of comparing the raw string. Also:
`klorb/tests/klorb/session/mixins/test_session_escalate_privileges.py`,
`test_session_ask_user_questions.py`, `klorb/tests/klorb/tools/test_bash.py` (for the
`is_success()`-driven `business_logic` envelope path specifically), `klorb/tests/klorb/tools/web/
test_fetch.py` (for the retired `"error"` dict keys), and a new `klorb/tests/klorb/tools/
test_response_envelope.py` covering `ToolResponseEnvelope.success`/`.error`/`to_wire_dict`'s
empty-list omission and `classify_exception`'s dispatch table directly.

## Documentation follow-ups (for the implementer, once the design above is confirmed)

* `docs/specs/session-and-turns.md`'s "How it works" section documents the `tool_response`
  dispatch loop today; extend it with the envelope shape and the category table above, and a
  short note on system-interjection delivery now happening on both the user-turn prompt (XML,
  unchanged) and the first tool_response of a round (JSON, new).
* New ADR: wrapping every `tool_response` in a structured JSON envelope (why -- Anthropic's
  structured-tool-response guidance, cross-tool error consistency) and, specifically, the Bash
  `response_body`-survives-`is_error=true` deviation from "`None` in the error case" (why --
  preserving `stdout`/`stderr` on a failed command matters more than contract purity; see "Design
  decision" above for the reasoning to carry over).
* Cross-reference from `docs/adrs/standing-interjections-complement-one-shot-for-level-triggered-
  state.md`, noting the registry now has two delivery mechanisms (XML onto a user prompt, JSON
  onto a tool_response), not a design change to the registry itself.

## Out of scope

* Populating `user_interjections` (delivering actually-queued user messages mid-tool-loop) --
  the field is reserved, empty, and documented in the system prompt, but nothing produces a
  non-empty list yet. Log this as a TODO.md follow-up under a `### Plan 015` heading once this
  plan is archived, per docs/plans/README-PLANS.md.
* A `"transient"` classification for the model-provider-level API call itself (e.g. an actual
  429 from `_send_and_receive`/`klorb.openrouter`) -- that's a turn-level failure, not a
  tool-call one, and doesn't flow through `tool_response` messages at all.
* Any change to `ToolCallEvent`, `SessionStatistics.tools`, or TUI rendering
  (`klorb/src/klorb/tui/mixins/rendering.py`) -- all keep consuming plain `result`/`error`
  exactly as today; nothing about how a call is displayed changes.

## Verification

* `make -C klorb lint typecheck`
* `make -C klorb test` (full suite -- the blast radius on `test_session.py` in particular means a
  real regression is more likely to show up there than in a narrowly-scoped new test)
* New `klorb/tests/klorb/tools/test_response_envelope.py` per "Tests to update" above.
* Manually inspect one full transcript (`--log-tool-calls`, or a TUI session) for a mixed
  success/`ValueError`/`PermissionError`/Bash-failure/WebFetch-timeout/WebFetch-429/
  WebFetch-401-or-403 run, confirming the envelope JSON is well-formed and the model still
  behaves sensibly reading it (no regression in task completion behavior from the added JSON
  wrapping).
