# EditFile's line-hint aliases are bare no-description schema properties, not an additionalProperties relaxation

* Date: 2026-07-18 16:00
* Question: `EditFileCore`'s `old_text` form accepts `line`/`line_num`/`line_no`/`line_number` as
  alternate spellings of the line hint (a model reaching for a different name than the
  schema-advertised `start_line` should still succeed rather than get a rejected call). Every
  edit tool's JSON schema sets `additionalProperties: false`, so any accepted argument name must
  appear in `properties` somehow. Should these aliases be declared as ordinary schema
  properties (each with its own description), or should `additionalProperties` be relaxed to
  admit arbitrary extra names instead?
* Answer: Declare each alias as a bare `{"type": "integer"}` property, with no `description`, in
  `EditFileCore.parameter_properties()` — kept `additionalProperties: false`. Their meaning is
  documented once, in the system prompt's edit examples, not per-property.
* Reasoning: `parameter_properties()` is inlined into every edit tool's definition and paid for
  on every turn, so a fourth full property description (repeating "alternate spelling of
  start_line") would be pure repeated-token cost for a rarely-used tolerance net. A bare
  `{"type": "integer"}` property costs only its name in the schema. Relaxing
  `additionalProperties` instead would have been cheaper still in schema size, but it opens the
  door to *any* unrecognized argument name silently passing schema validation — including typos
  unrelated to the line-hint aliases — trading a small, enumerable tolerance net for an
  unbounded one. Keeping `additionalProperties: false` and listing the exact accepted aliases
  is safer for the same practical cost.
