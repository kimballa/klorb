# EditFile's relaxed schema uses a minimal required list plus apply()-level validation, not a JSON-schema anyOf

* Date: 2026-07-18 16:00
* Question: `EditFileCore`'s widened argument matrix means `start_text`/`end_text`/`end_line` are
  each required in some accepted forms and forbidden or optional in others (e.g. `old_text`
  replaces `start_text`/`end_text` entirely). JSON Schema has a construct for exactly this —
  `anyOf`/`oneOf` branches, each with its own `required` list — expressing "either
  start_text-or-old_text" declaratively. Should the edit tools' `parameters()` schema express the
  accepted-forms matrix that way, or should `required` just list the fields common to every form,
  with the rest of the matrix validated inside `apply()`?
* Answer: Relax each edit tool's `required` to the fields every form actually needs
  (`filename`/`namespace`+`filename` where applicable, `start_line`, `new_text`) and move all
  cross-field validation — which combination of `start_text`/`end_text`/`old_text`/`end_line` is
  present, and whether it's a legal combination — into `EditFileCore._normalize_edit_args()`,
  raising a specific `ValueError` for each rejected shape. No `anyOf`/`oneOf` in the schema.
* Reasoning: `anyOf`/`oneOf` support is inconsistent across the model providers/tool-calling
  implementations this harness targets, and even where honored, it would meaningfully bloat a
  schema that's repeated in full on every turn (multiple full branch definitions instead of one
  flat property list). In-`apply()` validation with explanatory error messages is already this
  codebase's idiom for cross-field rules that a flat JSON schema can't express cleanly (e.g. the
  existing `start_line < 1` / `end_line < start_line` range check). Relaxing `required` doesn't
  lose any guardrail — the check still runs, just after the call reaches `apply()` rather than
  being rejected by the provider's own schema validator — and it moves the guardrail somewhere
  that can explain itself with a message naming the specific problem, which a bare schema
  rejection cannot.
