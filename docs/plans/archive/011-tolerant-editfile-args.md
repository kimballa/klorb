# Tolerant EditFile arguments

> **Draft.** This document describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented, and nothing else should depend on it,
> until it's promoted to `ready/` and then archived.

## Summary

`EditFileCore` (the drift-tolerant row-extent substitution shared by `EditFileTool`,
`EditScratchpadTool`, and `EditMemoryTool` — see
[[read-edit-file-scratchpad-share-core-via-composition]]) currently demands a rigid five-field
call: `start_line`, `end_line`, `start_text`, `end_text`, and `new_text`, all required. Agents in
practice reach for edits that don't fit that shape cleanly — a single-line change where naming an
`end_line`/`end_text` equal to the start is pure ceremony, or a small multi-line block they'd
rather hand over verbatim as one string than split into a start anchor, an end anchor, and a
line-number span. Every time the call doesn't fit, the model burns a rejected tool call (and
often a recovery `ReadFile`) learning the exact ritual.

This plan widens the *accepted* argument surface of `EditFileCore.apply()` without widening its
*semantics*: every new form desugars into the same "locate an inclusive `[start_line, end_line]`
row extent near the hint, tolerating bounded drift, then substitute `new_text`" mechanic the
[[edit-file-tolerates-bounded-line-drift-via-local-candidate-search]] ADR already established.
Two convenience shapes are added:

* **A single-line shortcut.** When `start_line == end_line`, `end_text` may be omitted or empty;
  `start_text` alone anchors the one line being replaced.
* **An `old_text` block form.** Instead of `start_text`+`end_text`, the caller may supply
  `old_text`: the *entire* contiguous block to replace, verbatim, as one multi-line string. It's
  fuzzy-anchored to the line hint with the same drift window; `end_line` becomes optional
  (inferred by counting `old_text`'s lines) and, when supplied, must agree with that count. In
  `old_text` mode the line hint may also be spelled `line`, `line_num`, `line_no`, or
  `line_number`.

The prose that teaches an agent *when* to prefer each form lives in the system prompt's edit
examples, not in per-argument descriptions — `EditFileCore.parameter_properties()` is inlined
into every edit tool's schema and paid for on every turn, so its text stays terse.

This is purely additive: the classic five-field call is still valid and still the recommended
form for long replacement spans. Nothing about the on-disk result, the drift search, the
"Ambiguous match" recovery, or the result dict changes.

Separately, the plan also sharpens the feedback for calls that don't even parse as JSON (a total
tool-call failure, before any tool runs) — precise break-point offsets, an XML-vs-JSON detector,
a common-escaping-mistakes primer, and an edit-argument-specific escaping nudge — since the edit
tools are the biggest source of the large, heavily-escaped string arguments where JSON escaping
goes wrong. See "Tool-call argument parse errors" below.

## Motivation

Two eval-adjacent frictions motivate this:

1. A one-line change forces the model to state the same line number twice (`start_line ==
   end_line`) and the same content twice-ish (`start_text` for line N, `end_text` for line N).
   For a single line those are literally the same line, so `end_text` is redundant ceremony that
   the model still has to get byte-perfect or eat a rejection.
2. A small multi-line replacement (say 2–5 lines) forces the model to mentally split the block
   into "first line → `start_text`", "last line → `end_text`", and "count the lines to get
   `end_line`". Models miscount `end_line`, or capture the wrong last line, and the call is
   rejected. Handing over the whole block as `old_text` and letting the harness count is both
   easier for the model and less error-prone.

Neither friction justifies loosening the safety property the drift-search ADR is careful about
(never silently editing the wrong location). Every new form still fails closed on a zero- or
multi-candidate match exactly as today.

## Current state (what exists today)

* `EditFileCore.apply()` (`klorb/src/klorb/tools/util/edit_file_core.py`) requires
  `start_line`, `end_line`, `start_text`, `end_text`, `new_text`; `context_before`/
  `context_after` are optional disambiguators.
* `start_text`/`end_text` are each truncated to their first line (with advisory `feedback`) if a
  model accidentally pastes a multi-line block, then hard-checked to be exactly one line.
* `_resolve_line_range_edit()` → `_find_drift_candidates()` locates `start_text` at
  `candidate_start` and `end_text` at `candidate_start + (end_line - start_line)`, within
  `ProcessConfig.edit_file_drift_search_radius` (default 20) of the hint. Exactly one candidate
  applies; zero raises a precise mismatch error; two-plus raises "Ambiguous match" naming each
  candidate's `context_before`/`context_after`.
* `parameter_properties()` returns the shared JSON schema; each tool wraps it with its own
  `filename` (or none, for scratchpad) and a `required` list. `EditFileTool.parameters()` sets
  `required = ["filename", "start_line", "end_line", "start_text", "end_text", "new_text"]` and
  `additionalProperties: False`.
* Callers: `EditFileTool`, `EditScratchpadTool`, `EditMemoryTool` all delegate to a
  `self.edit_file_core`. Any schema/`required` change must be mirrored in all three.

## The widened argument matrix

The single source of truth for what's accepted. "Line hint" = `start_line`, or (only in
`old_text` mode) one of its aliases. All forms still require `new_text` and a line hint.

### Accepted forms

| # | Form | Anchor | `end_line` | Result |
| --- | ------ | -------- | ----------- | -------- |
| 1 | Classic | `start_line`, `end_line`, `start_text`, `end_text` | required | Existing behavior, unchanged. |
| 2 | Single-line shortcut | `start_line == end_line`, `start_text`, `end_text` omitted/`""` | required (`== start_line`) | Replace the one line at the hint, anchored on `start_text`. |
| 3 | `old_text`, explicit span | `old_text`, `start_line`, `end_line` | required, **must** equal `start_line + count(old_text) - 1` | Replace the block; verified against `old_text`. |
| 4 | `old_text`, inferred span | `old_text`, `start_line` (or alias) | omitted | `end_line` inferred from `count(old_text)`. |
| 5 | `old_text` one-liner | `old_text` (1 line), `start_line` (or alias) | omitted | Single-line replace; a natural instance of form 4. |
| 6 | Multi-line `start_text` as `old_text` | `start_text` (multi-line), `end_text` omitted/`""`, `start_line` | omitted or explicit | `start_text` is re-interpreted as `old_text` (forms 3/4). |

Notes:

* **Form 2** — the single-line shortcut requires `start_line` and `end_line` to be *equal and
  both present*. It does not let `end_line` be omitted in `start_text` mode; a missing `end_line`
  with single-line `start_text` and no `old_text` is an error (there's no block to count and no
  second anchor). This keeps the "omit `end_line`" affordance exclusively in `old_text` mode,
  where it's unambiguous.
* **Form 2 vs. Form 3 — are they the same?** (raised in review.) No — they take *different
  arguments*. Form 2 anchors on `start_text` (the classic anchor field) and is strictly one line;
  Form 3 anchors on `old_text` and generalizes to any number of lines. They *converge in effect*
  only in the single-line case (a 1-line `old_text` with `end_line == start_line` produces the
  same edit as Form 2), and that convergence is deliberate: we accept both so an agent that
  reaches for either field name for a one-liner succeeds, rather than having to remember which one
  the tool "wants." The forms are kept as separate table rows because their *argument shapes*
  differ, not because they describe two different edits — that's the whole point of widening the
  accepted surface. Neither is removed.
* **Form 6** — precedence rule: multi-line content in `start_text` is treated as `old_text`
  **only when `end_text` is empty or missing**. A multi-line `start_text` *with* a non-empty
  `end_text` keeps today's behavior: `start_text` is truncated to its first line with the
  existing advisory `feedback`, and `end_text` anchors the end. This preserves back-compat for
  any caller relying on the truncation nicety while giving the "I pasted the whole block into
  `start_text`" case a useful meaning.
* **Aliases** — `line`/`line_num`/`line_no`/`line_number` are consulted **only when `old_text`
  (or a form-6 multi-line `start_text`) is present**. In classic/`start_text` mode only
  `start_line` names the hint. If both `start_line` and an alias are present with the same value,
  fine; with conflicting values, error (see below).
* **`old_text` verification depth** — `old_text` is checked against the file *in full* (every
  line of the block, not just its first and last), reusing the fuzzy-whitespace fallback
  per-line. The caller already paid the tokens to send the whole block, so verifying all of it is
  free safety and strictly stronger than the endpoints-only check the `start_text`/`end_text`
  form uses. See "Design decisions" for why this doesn't reintroduce the token concern the
  drift-search ADR guards.

### Rejected forms (must raise a precise `ValueError`)

| Condition | Error intent |
| ----------- | -------------- |
| `old_text` **and** (`start_text` **or** `end_text`) both present | "Provide `old_text`, or `start_text`/`end_text`, not both." |
| `old_text` present, `end_line` present, `count(old_text) != end_line - start_line + 1` | Name both counts; suggest dropping `end_line` to let it be inferred. |
| Neither `old_text` nor `start_text` present | "No anchor: provide `start_text` (+`end_text`) or `old_text`." |
| No line hint in any spelling | Existing "Missing required argument" style, listing accepted spellings. |
| `start_line` and an alias both present with **different** values | "Conflicting line hints: `start_line=A` vs `line=B`." |
| Single-line `start_text` (no `old_text`), `end_text` omitted, `end_line != start_line` | "Omit-`end_text` is only for single-line edits (`start_line == end_line`); otherwise supply `end_text` or use `old_text`." |
| `new_text` missing | Existing behavior, unchanged. |
| `end_line < start_line`, or hint out of range | Existing behavior, unchanged. |
| `old_text == ""` (empty) | "`old_text` must be the non-empty block to replace; to insert into an empty file use `start_line=1, end_line=0, start_text=\"\", end_text=\"\"`." |

The empty-file insertion path (`start_line=1, end_line=0, start_text="", end_text=""`) is
untouched and remains the sole insert-into-empty form; it is deliberately *not* re-expressed in
`old_text` terms (there is no block to anchor).

**Inserting after an existing line** (into a non-empty file) needs no special form: "replace" the
line you want to insert after with *itself plus the new content*. The single-line shortcut makes
this compact — e.g. to add a footer line right after line 12 (`return result`):

```json
{ "filename": "foo.py", "start_line": 12, "end_line": 12,
  "start_text": "    return result",
  "new_text": "    return result\n    # end of helper" }
```

`start_text` still anchors the one real line; `new_text` carries that line back verbatim followed
by the inserted line(s). A worked version of this goes in the system prompt (see below).

## Implementation shape

All logic lands in `EditFileCore`; the three tool wrappers change only their `required` lists and
schema.

### 1. Argument normalization (new private step in `apply()`)

Before the existing type checks, add a `_normalize_edit_args(args)` step that returns a resolved
`(start_line, end_line, start_text, end_text, new_text, context_*)` tuple (or raises). It:

1. Reads `new_text` (unchanged requirement).
2. Resolves the **line hint**: `start_line` if present; else, only if `old_text`/form-6 applies,
   the first present alias among `line`, `line_num`, `line_no`, `line_number`. Conflicting
   distinct values → error.
3. Detects **`old_text` mode** (`old_text` present and non-empty) and **form-6** (multi-line
   `start_text` with empty/missing `end_text`); form-6 sets `old_text = start_text` and clears
   `start_text`. Enforces the "not both" and empty-`old_text` rejections.
4. In `old_text` mode: split `old_text` into lines; derive `start_text = lines[0]`,
   `end_text = lines[-1]`, `block_len = len(lines)`. If `end_line` given, verify `end_line -
   start_line + 1 == block_len` (mismatch → error); else set `end_line = start_line + block_len -
   1`. Stash the full `old_text` line list so `_resolve_line_range_edit` can verify the interior
   (see step below).
5. In single-line `start_text` mode with `end_text` omitted/empty: require `end_line ==
   start_line` (or `end_line` omitted → treat as `start_line`); set `end_text = start_text`.
6. Otherwise (classic): behave exactly as today, including the multi-line-`start_text`-with-
   `end_text` truncation nicety.

Keeping this as one normalization function means `_resolve_line_range_edit` and
`_find_drift_candidates` stay almost as they are — they still receive a concrete `start_text`/
`end_text`/`start_line`/`end_line`. The one addition they need is an optional
`block_lines: list[str] | None` (the full `old_text` lines) so a candidate is accepted only if
the whole span matches, not just its endpoints. When `block_lines is None` (classic/`start_text`
form) the endpoints-only check is unchanged.

### 2. Full-block verification for `old_text`

`_find_drift_candidates` gains an optional `block_lines` parameter. When present, a candidate at
`candidate_start` must satisfy `all_lines[candidate_start-1 : candidate_start-1+block_len]`
equals `block_lines` (exact), with the fuzzy pass comparing `.strip()` per line — supplanting the
first/last-line-only anchor check for that call. `context_before`/`context_after` still apply
unchanged. This is a strict superset of the endpoints check, so a genuine wrong-location match is
*less* likely, never more.

### 3. Result dict / feedback

Unchanged shape (`requested_start_line`, `requested_end_line`, `start_line`, `end_line`,
`line_hint_matched`, `new_total_lines`, `content`, optional `fuzzyWhitespaceMatch`/`whitespace`/
`feedback`). When `end_line` was inferred (not supplied), `requested_end_line` echoes the
inferred value.

**When to emit `feedback` (raised in review — would per-form feedback help or just confuse?).**
The rule: emit `feedback` only when the harness did something the agent might not have *intended*,
never for a clean use of a first-class form. Concretely:

* **Clean forms 2–5** (single-line shortcut, any `old_text` form) — **no feedback**. These are
  supported idioms; nudging on them would be noise and would wrongly imply the agent did something
  off-pattern.
* **Form 6 — the implicit `start_text` → `old_text` conversion** — **do** emit a terse feedback
  ("btw, `start_text` held multiple lines with no `end_text`, so it was treated as `old_text`; you
  can pass `old_text` directly next time"). Here the agent may not realize a reinterpretation
  happened, so surfacing it — advisory, after a successful edit — teaches the direct form without
  blocking anything. This matches the precedent already set by the multi-line-`start_text`
  truncation advisory the codebase emits today, so it's a known-non-confusing pattern.
* **Alias spelling** — emit the existing terse note ("the line hint is `start_line`; `line_no` was
  accepted this time").

All of these stay advisory `feedback`, never errors, consistent with the existing token-saving
nudges.

### 4. Schema and `required` changes (all three tool wrappers + core)

* `parameter_properties()` adds:
  * `old_text` — one terse line: "Alternative to start_text/end_text: the entire current block
    to replace, verbatim. end_line then optional (inferred from its line count)."
  * `line`, `line_num`, `line_no`, `line_number` — bare `{"type": "integer"}` entries with **no
    description** (near-zero repeated-token cost), so `additionalProperties: False` still admits
    them. Their meaning is documented once in the system prompt, not per-property.
* `start_text`, `end_text`, `end_line` descriptions get a terse "(optional in some forms — see
  system prompt)" and are **removed from every wrapper's `required` list**. New `required`:
  * `EditFileTool`: `["filename", "start_line", "new_text"]`
  * `EditScratchpadTool`: `["start_line", "new_text"]`
  * `EditMemoryTool`: mirror scratchpad (whatever identifying field it carries) + `new_text`.
  * (The line hint stays in `required` as `start_line`; aliases are a tolerance net for when a
    model ignores the schema, not the advertised spelling.)

Because the accept/reject logic now lives in `apply()` with rich `ValueError`s, relaxing the
schema `required` doesn't lose any guardrail — it moves the guardrail somewhere that can explain
itself.

## Prompt changes (system prompt, not arg descriptions)

Per the terseness constraint, `parameter_properties()` text stays minimal; the teaching goes in
`klorb/src/klorb/resources/system_prompts.d/default_sys.md`'s "Editing files" section, which
already carries the worked example. Add:

* A one-line **decision rule** near "The rule:":
  > For a long multi-line replacement, `start_line`/`end_line` + one-line `start_text`/`end_text`
  > is the most token-efficient anchor (you never repeat the interior). For a short block (~1–5
  > lines), `old_text` (the whole block, verbatim) is easier and often more compact — the harness
  > infers `end_line` for you. Never send both.
* A note on the **single-line shortcut**: when replacing exactly one line, set
  `start_line == end_line` and omit `end_text`; `start_text` alone anchors it.
* A short **insert-after worked example** (call + response) showing the "replace a line with
  itself plus a footer" idiom — the existing prose already says to fold the original line into
  `new_text`, but a concrete example removes the ambiguity of how, and pairs naturally with the
  single-line shortcut note above.
* A **second worked example** alongside the existing one, showing the `old_text` form for a
  2–3 line change (call + response), so the model sees both idioms side by side. Keep the
  existing `start_text`/`end_text` worked example as the long-span exemplar.
* One line reminding that `old_text` is fuzzy-anchored to the line hint with the same drift
  tolerance, so a slightly-stale line number still resolves.

`EditMemory`/`EditScratchpad` inherit this section (the memories note already says "same edit
mechanics as EditFile above"); no per-tool prose duplication.

## Tool-call argument parse errors

The forms above make a *well-formed* JSON call more forgiving. This section covers the case one
level earlier: the model's `arguments` string doesn't parse as JSON *at all*, so no tool's
`apply()` (and none of the desugaring above) is ever reached. Today this is handled in
`Session._run_tool_calls` (`klorb/src/klorb/session.py`), which on `json.JSONDecodeError` sends
back exactly `f"Invalid JSON in tool call arguments: {json_exc}"` and blanks `call.arguments` to
`"{}"` so the malformed string isn't replayed to the API. That one line is the whole feedback the
model gets — and a total parse failure is the most expensive kind, because the model learns
nothing precise and typically retries the same malformed shape. This is worth investing a
multi-line, teaching response in: better the agent learns exactly how to quote and escape than
burn three rejected calls rediscovering it.

Most of this is **tool-agnostic** — a malformed `arguments` string is malformed for any tool — so
it lands in `session.py`'s decode-error branch (or a small `klorb.tools.util` /
`klorb.tools.tool` helper it calls), not in `EditFileCore`. The last bullet is edit-specific and
keys off the edit tools' own argument names. It's in this plan because the edit tools are by far
the biggest producers of large, heavily-escaped string arguments (`start_text`/`end_text`/
`old_text`/`new_text` routinely carry quotes, backslashes, and newlines), so they're where JSON
escaping goes wrong most often — but the general improvements should be written to benefit every
tool.

### 1. Be precise about *where* the JSON broke

`json.JSONDecodeError`'s `str()` already carries `line N column M (char K)` and a reason
(`"Expecting ',' delimiter"`, `"Unterminated string starting at"`, etc.), and today's message
does interpolate the whole exception — so the offset is technically present but buried. Make it
explicit and structured: surface `json_exc.lineno`, `json_exc.colno`, `json_exc.pos`, and
`json_exc.msg` as named parts, and quote the offending fragment — a short window of the raw
string around `json_exc.pos` (e.g. ~40 chars each side, with a caret/marker at the position) so
the model sees the exact spot, not just a number it has to count to. Confirm the raw string is
still available at that point (it is: `raw_arguments = call.arguments` is captured before the
blanking).

### 2. Detect XML-shaped input and say so specifically

If the first non-whitespace character of the raw `arguments` string is `<`, the model almost
certainly emitted XML/markup instead of JSON. Short-circuit to a specific message rather than the
generic decode error: state that the arguments look like XML/markup but the tool-call ABI is
**JSON only**, and include a short concrete example of what a good call looks like, e.g.:

```
{ "filename": "foo.py", "start_line": 12, "new_text": "..." }
```

so the model has a correct shape to pattern-match against. (One inline example, not a per-tool
one — the point is "JSON objects look like this," not a schema dump.)

### 3. In the general syntax-error case, teach the common mistakes

When it's a genuine JSON syntax error (not the XML case), append a compact multi-line reminder of
the handful of mistakes that actually cause these, each with a *bad → good* contrast so the
lesson is concrete rather than abstract. Cover at least:

* **Unescaped double quotes inside a string** (the dominant failure for edit tools) — emphatic,
  because it's both the most common and the least obvious:
  `"new_text": "print("hi")"` → `"new_text": "print(\"hi\")"`.
* **Mismatched / unbalanced brackets or braces** — a missing or extra `}`/`]`.
* **Comma problems** — a trailing comma before `}`/`]`, or a missing comma between members:
  `{"a": 1 "b": 2}` → `{"a": 1, "b": 2}`.
* **Mismatched quotes** — a string opened with `"` and never closed, or single quotes used for a
  JSON string (`'x'` → `"x"`).

Keep each item to a line or two; the whole block is a fixed teaching string, not generated per
error. It's deliberately multi-line — the token cost of one good explanation is far below the
cost of several repeated failing calls.

### 4. Edit-argument-specific escaping hint

If any of the literal substrings `start_text`, `end_text`, `old_text`, or `new_text` appear in
the raw (unparsed) `arguments` string, add an emphatic, targeted note: the call was almost
certainly an edit tool carrying file content in one of those fields, and file content is exactly
what breaks JSON when its embedded double quotes and backslashes aren't escaped — so **double-
check the quoting and escaping of those argument values specifically**. This heuristic is a cheap
substring scan of the raw string (no parse needed, since parsing already failed) and is the one
piece that's edit-aware; gate it so it only fires when one of those names is actually present, so
a malformed call to some other tool doesn't get an irrelevant edit lecture.

### Where this lives

The decode-error branch at `session.py:1462` builds the `error` string; factor the assembly
(offset framing, XML short-circuit, common-mistakes block, edit-arg substring check) into a
dedicated helper (proposed: `klorb.tools.tool.describe_tool_arg_json_error(name, raw_arguments,
json_exc)` alongside the existing `default_invalid_tool_call_detail`) so it's unit-testable
without driving a whole `Session`, and so both the model-facing `error` and the UI-facing
`default_invalid_tool_call_detail` can draw from the same source. `statistics.malformed_tool_calls`
accounting and the `call.arguments = "{}"` blanking are unchanged.

## Tests (`klorb/tests/test_edit_file.py`)

The widened matrix multiplies the argument combinations, so enumerate and unit-test them all
against `EditFileCore.apply()` with mocked args (these tests exercise `apply()` logic, not a live
model — that's what evals are for). Group them:

**Accepted forms — each asserts the correct file bytes and result dict:**

1. Classic five-field (regression — still works).
2. Single-line shortcut: `start_line == end_line`, `end_text` omitted.
3. Single-line shortcut: `start_line == end_line`, `end_text = ""`.
4. `old_text` multi-line, `end_line` omitted (inferred).
5. `old_text` multi-line, `end_line` supplied and matching.
6. `old_text` one line, `end_line` omitted.
7. Form-6: multi-line `start_text`, `end_text` omitted → treated as `old_text`.
8. `old_text` with each alias spelling (`line`, `line_num`, `line_no`, `line_number`), one test
   each, `start_line` absent.
9. `old_text` with `start_line` *and* a matching-value alias present (dedup, no error).
10. `old_text` block that has drifted from the hint within the radius → relocates and applies
    (`line_hint_matched=False`), full-block verified.
11. `old_text` block whose interior line differs from the file → **no** candidate → precise
    mismatch error (proves full-block verification, not endpoints-only).
12. `old_text` with a fuzzy-whitespace-only difference on one interior line → resolves via the
    fuzzy pass, sets `fuzzyWhitespaceMatch`.

**Rejected forms — each asserts a `ValueError` whose message names the specific problem:**

 1. `old_text` + `start_text` both present.
 2. `old_text` + `end_text` both present.
 3. `old_text` + `end_line` present but line count disagrees.
 4. Neither `old_text` nor `start_text`.
 5. No line hint in any spelling.
 6. `start_line` + alias with conflicting values.
 7. Single-line `start_text`, `end_text` omitted, `end_line != start_line`.
 8. `old_text = ""`.
 9. Multi-line `start_text` *with* non-empty `end_text` → still truncates + advisory (assert
    `feedback` present and edit lands on first-line anchor), i.e. form-6 does **not** trigger.
10. `new_text` missing (regression).
11. Out-of-range / `end_line < start_line` (regression).

Parametrize where the bodies are identical across spellings (e.g. #8's four aliases). Mirror a
representative subset for `EditScratchpad`/`EditMemory` via their existing test modules to prove
the shared core reaches all three tools (no need to re-run the full matrix three times — one
smoke test per wrapper that the new forms are wired through).

**Parse-error helper tests** (new, alongside `tool.py`'s tests — the helper is tool-agnostic, so
these don't belong in `test_edit_file.py`): drive `describe_tool_arg_json_error()` directly with
raw strings and assert on the returned message:

* offset framing names the correct `line`/`column`/`char` and quotes the fragment around the
  break point;
* a raw string whose first non-whitespace char is `<` yields the XML-specific message with the
  JSON example, and does **not** emit the generic common-mistakes block;
* an unescaped-inner-quote string yields the common-mistakes block including the escape contrast;
* a raw string containing `new_text` (still malformed) yields the edit-argument escaping note;
* a malformed call to a non-edit tool (none of the four names present) does **not** get the
  edit-argument note.

## Evals (`klorb/evals/cases.py`)

**Answering the question posed in the task — should we stress single vs. multi-line / repeated
edits more?** Yes, and specifically to prove the *new forms actually get used*, not just that the
file ends up right. Evals grade filesystem state (per
[[grade-tool-evals-by-filesystem-state]]), but several existing cases already inspect
`session.messages` for the arguments actually sent (the ambiguous-match cases do this). We use the
same "where useful" carve-out to assert the *shape* of the call where the whole point is that a
new shape was available. Proposed new cases:

1. **`edit_file_single_line_shortcut`** — a one-line change; assert the file is right *and* that
   at least one `EditFile` call used the shortcut (`end_text` absent/empty with `start_line ==
   end_line`). Prioritizes proving form 2 is reachable. **(Priority 1.)**
2. **`edit_file_old_text_small_block`** — replace a 3-line block; assert result + that an
   `EditFile` call carried `old_text` and omitted `end_line`. Proves forms 3–4. **(Priority 1.)**
3. **`edit_file_old_text_vs_start_end_long_span`** — a ~15-line replacement where `start_text`/
   `end_text` is the efficient form. Since we don't do expected-token counting today, this case is
   only useful if it **inspects `session.messages`** and checks that the edit used
   `start_text`/`end_text` and did **not** send the whole span as `old_text`. Grading is
   three-way: correct file state *and* start/end used → **pass**; correct file state but the span
   was sent as `old_text` → **conditional pass** (the edit worked, it was just the token-wasteful
   shape); wrong file state → **fail**. This needs a small harness affordance for a check that can
   report "correct-but-suboptimal" as conditional rather than failure — see below. Companion to #2,
   covering the "long span → prefer start/end" half of the decision rule. **(Priority 3.)**
4. **`edit_file_old_text_drifted`** — the `old_text` block has been shifted by an earlier edit in
   the same turn; assert it still lands (drift + full-block verification). **(Priority 2.)**
5. **`edit_file_old_text_repeated_block`** — two identical multi-line blocks in the file; the
   `old_text` full-block match plus a line hint should resolve the intended one without needing
   `context_*` (contrast with the single-line ambiguous-match cases, where full-block context
   isn't available). Assert the correct block changed. **(Priority 2.)**
6. **`edit_file_many_old_text_ops_one_turn`** — several small `old_text` block edits in one turn,
   bottom-to-top, mirroring `EDIT_FILE_MANY_OPS_BOTTOM_TO_TOP` but for the block form. Lower
   priority; #4 already covers the drift interaction. **(Priority 4.)**

Recommend landing priorities 1–2 (cases 1, 2, 4, 5) with this plan and holding 3 and 6 unless the
first four surface gaps. Add each to the `CASES` list and give each a realistic
`expected_tool_calls` so a stumbled retry shows as a conditional pass.

**Harness affordance — a check that can report "conditional".** Today `CaseResult.conditional` is
derived solely from `num_tool_calls > expected_tool_calls` (see
[[eval-conditional-pass-on-excess-tool-calls]]); a `check()` can only say pass (`None`) or fail (a
string). Case 3 wants a third outcome — *correct file state via a suboptimal call shape* — that
should surface as a conditional pass, not a hard fail (a wasteful-but-correct edit is a yellow
flag, exactly what "conditional" already means) and not a silent pass. Extend the check contract
minimally: let `EvalCase` carry an optional second `soft_check` (same signature as `check`) run
only when `check` passes; a non-`None` return marks the result `conditional` with that string as
the reason, without flipping `passed` to `False`. `run_case()` sets a new
`CaseResult.soft_failure_reason`, and `CaseResult.conditional` becomes "over tool-call budget **or**
a soft-check reason is set." This keeps the existing count-based path untouched and gives case 3
(and any future "did it use the efficient shape?" case) a clean home. Case 3's `soft_check`
inspects `session.messages` for an `old_text` argument spanning the long region and returns a
reason if found.

### Per-case generated-token counts in the report

The harness already carries a `tiktoken` estimator (`_encoding_for_model()`, used by
`tool_token_counts()`), so it's cheap to also report how many tokens each case's run *generated* —
a direct read on the "is the model being verbose / using wasteful call shapes?" question this plan
cares about, and the closest proxy we have to cost without real token accounting. Add a per-case
generated-token estimate to the summary: sum the estimator's count over the case's assistant
output — its tool-call `arguments` strings (already captured verbatim in
`CaseResult.tool_call_log`) plus `final_response`. This is an informational column for the user to
read, **not** a graded threshold (per Out of scope). It pairs with case 3: a run that used
`old_text` for a long span will visibly carry more generated tokens than one that used
`start_text`/`end_text`.

### Eval harness `--self-review` flag

Add a `--self-review` argument to `klorb/evals/run_evals.py` that closes the loop: feed the eval
output back to the agent under test and ask it to propose tool improvements.

Behavior when `--self-review` is passed:

1. Run the suite as normal — which already writes the full detailed log to `EVAL_LOG_PATH`
   (`evals.log`) via `_write_eval_log()`.
2. Mint an agent-readable temp directory (see the helper below) and **copy the existing
   `evals.log` into it** with the standard filesystem utils, rather than re-rendering the output a
   second way — the log the harness already produces is exactly the artifact we want the agent to
   read. The temp directory is registered for removal on process exit (see helper).
3. Start a review `Session` (same model/provider) whose `read_dirs.allow` includes that temp
   directory, with a prompt that:
   * tells it the path to the copied results file and **reminds it to use its `ReadFile` tool** to
     read it (it can page a long file);
   * **explains the log's grading vocabulary** — pass (correct and efficient), **conditional pass**
     (correct file state but off-pattern: more tool calls than expected, or a suboptimal call
     shape — a yellow flag, still counted as passing), and failure (wrong result or an error) — so
     it interprets `[CONDITIONAL PASS]` entries correctly rather than treating them as either clean
     or broken;
   * asks it to diagnose which **tool** ergonomics caused failures and retries and propose concrete
     changes that would raise the success rate — scoped to the tools in general, not only the edit
     tools (parse-error feedback, `ReadFile` pagination, `ListDir`, etc. are all fair game);
   * instructs it to **number and prioritize** its suggestions and emit the **top five at most**.
4. Surface the agent's self-diagnosis message to the user through the **same output path the rest
   of the run already uses** — the eval console output is tee-captured to the fs logger, so writing
   the diagnosis there (not a bespoke `print`) means it reaches the user's terminal and the
   captured log identically to every other line of the run.

**The temp-dir + read-grant helper.** The task flags that "mint a temp dir and grant an agent
`readDirs` on it" should be a library function; we don't have one today (the scratchpad tools
*bypass* the permission tables entirely, per [[scratchpad-tools-bypass-permission-tables]], and the
eval harness builds `read_dirs=DirRules(allow=[workspace_root])` inline). Per review, it lands in
the **permissions package** (e.g. `klorb.permissions.directory_access`), not `tools.util` — it's
general directory/permissions plumbing, not tool-specific — with an **active name**, e.g.
`create_tempdir_readable_by(session_config, *, remove_on_exit=False)`. It:

* creates a `tempfile.mkdtemp()` directory and appends its `canonicalize_dir`-canonicalized path
  to the given `SessionConfig.read_dirs.allow` (returning the `Path`);
* takes a `remove_on_exit: bool = False` flag. When `True`, the directory is appended to a
  module-global list; the **first** time anything is added, an `atexit` handler is registered
  once; that handler `shutil.rmtree`s every directory in the list on process exit. When `False`
  (the default), the caller owns cleanup, exactly as today's inline callers do. This gives the
  eval path automatic teardown (`remove_on_exit=True`) without every caller re-implementing it.

The eval `--self-review` path is its first caller. During implementation, grep for other inline
`DirRules(allow=[...])` "grant read on a scratch location" sites (the harness's own at minimum) and
migrate the ones that fit; if the eval path turns out to be the only one, still add the helper as
requested but keep it minimal.

Keep `--self-review` off by default (a normal `make evals` run is unchanged); it's an opt-in
investigative mode that costs a second model round-trip. **When it's disabled, the run's summary
should print a one-line suggestion** to re-run with `--self-review` for an agent-authored critique
of the tools, so the affordance is discoverable without reading the flag list.

## Design decisions (resolved in review)

All four were confirmed by the reviewer.

1. **Full-block `old_text` verification vs. endpoints-only.** *Decided: verify the whole block.*
   It's strictly safer and the tokens are already spent. The drift-search ADR's "don't
   recapitulate large spans just to anchor" concern was about *forcing* the caller to send interior
   content they otherwise wouldn't; here the caller *chose* `old_text` and sent it, so verifying it
   isn't extra burden.
2. **Aliases in schema as bare no-description properties vs. `additionalProperties` relaxation.**
   *Decided: bare `{"type": "integer"}` properties, keeping `additionalProperties: False`.*
   Cheaper and safer than opening the schema.
3. **`required` relaxed to `["filename", "start_line", "new_text"]`** (validation moved into
   `apply()`), rather than an `anyOf`/`oneOf` schema expressing "start_text-or-old_text".
   *Decided.* `anyOf` is poorly and inconsistently honored across providers and would bloat the
   repeated schema; in-`apply()` validation with explanatory errors is the codebase's existing
   idiom.
4. **Form-6 precedence** — multi-line `start_text` → `old_text` *only* when `end_text` is
   empty/missing; otherwise keep today's truncation. *Decided: keep the carve-out.* Rationale
   (from review): always treating a multi-line `start_text` as `old_text` would reopen the question
   of how to reconcile `old_text` with a supplied `end_text` (should `end_text` be the last line of
   `old_text`, or the line *after* it?), and those semantics aren't dialed in. Restricting the
   conversion to the `end_text`-absent case sidesteps that ambiguity entirely; if agents later
   stumble on the `start_text`+`end_text`+multi-line case in practice, we can revisit it then with
   evidence.

## Out of scope

* No change to the drift search radius, the "Ambiguous match" machinery, or `context_before`/
  `context_after`.
* No batch/multi-edit API (rejected in the drift-search ADR; unchanged here).
* No change to `ReadFile`/`ReplaceAll`/other tools.
* No *automated* token-cost pass/fail *check* in the eval report — grading stays state- and
  shape-based (case #3 uses a shape `soft_check`, not a token threshold). Printing per-case
  generated-token counts for the user to eyeball is in scope, though — see the evals section.
* Turning any of this plan's durable behavior into a spec happens at implementation time, per
  README-PLANS: fold the accepted-argument contract into `docs/specs/` and record the
  endpoints-vs-full-block and schema-`required` decisions as ADRs.

## Files touched (anticipated)

* `klorb/src/klorb/tools/util/edit_file_core.py` — normalization, `old_text`, aliases,
  single-line shortcut, full-block verification, schema properties.
* `klorb/src/klorb/tools/edit_file.py`, `.../tools/scratchpad/edit.py`,
  `.../tools/memory/edit_memory.py` — relaxed `required` lists.
* `klorb/src/klorb/resources/system_prompts.d/default_sys.md` — edit examples + decision rule.
* `klorb/src/klorb/session.py` — decode-error branch calls the new parse-error helper.
* `klorb/src/klorb/tools/tool.py` — new `describe_tool_arg_json_error()` helper (offset framing,
  XML short-circuit, common-mistakes block, edit-arg substring hint).
* `klorb/tests/test_edit_file.py` (+ small smoke additions to the scratchpad/memory edit tests);
  new parse-error-helper tests alongside `tool.py`'s tests.
* `klorb/evals/cases.py` — new cases (incl. case 3's `soft_check`).
* `klorb/evals/harness.py` — optional `EvalCase.soft_check` + `CaseResult.soft_failure_reason`
  wired into `CaseResult.conditional`; per-case generated-token estimate.
* `klorb/evals/run_evals.py` — `--self-review` flag and its review-session flow; the
  disabled-mode "re-run with --self-review" suggestion.
* `klorb/evals/report.py` — the per-case generated-token column in the summary.
* `klorb/src/klorb/permissions/directory_access.py` — the
  `create_tempdir_readable_by(..., remove_on_exit=False)` helper (with the `atexit`-registered
  global cleanup list).
* New ADR(s) under `docs/adrs/`; new/updated spec under `docs/specs/` at implementation time.
