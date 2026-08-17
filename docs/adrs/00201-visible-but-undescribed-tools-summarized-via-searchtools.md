# A visible-but-undescribed tool is summarized via an AdditionalTools interjection, not its full schema

## 2026-08-17

## Question

`Tool.default_visible()` already lets a tool be dispatched by name while never appearing in
`tool_definitions()` (the OpenAI-style `tools` array), so its schema doesn't cost tokens on every
turn -- but that also means the model never learns the tool exists at all, unless it happens to
guess the name. `ReplaceAll`'s schema is disproportionately large for how rarely it's needed
(literal/regex, case-insensitivity, multiline flags), a good candidate to keep off the `tools`
array, but hiding it completely means no model ever discovers it. How should a tool that's worth
naming, but not worth describing on every turn, be advertised?

## Answer

Add `Tool.default_described()` (default `True`) alongside `default_visible()`. `ReplaceAll`
overrides it to `False`. `ToolRegistry.tool_definitions()` now requires both
`default_visible()` and `default_described()` (or the tool's name in `extra_visible_tools`,
which bypasses both gates at once) before including a tool's full schema.

A tool that's visible but not described is instead named — with a hard-truncated (80-char)
description, via `ToolRegistry.additional_tool_summaries()` — in a new one-shot `AdditionalTools`
`<SystemInterjection>`, built by `SessionPromptSetupMixin._build_additional_tools_interjection()`
and prepended on the first turn only, the same way `AvailableSkills` is. A new `SearchTools` tool
(`klorb.tools.search_tools.SearchToolsTool`) looks up a named tool's full definition on demand: a
single query that's the exact canonical name or alias of a registered tool returns it directly,
skipping the keyword search; otherwise every query is matched as a literal, case-insensitive
substring against every registered tool's name, description, and parameter schema.

## Reasoning

* This mirrors the `AvailableSkills`/`ActivateSkill` shape already established for skills: a
  cheap standing catalog naming everything available, with a full-detail lookup call for
  whichever entry turns out to matter. Reusing that shape means no new interjection lifecycle
  design was needed — `_additional_tools_seeded` is one more one-shot flag alongside
  `_skills_seeded`/`_memories_seeded`.
* Gating on both `default_visible()` and `default_described()` (rather than folding described-
  ness into visibility) keeps `default_visible()`'s existing meaning intact: a tool can still be
  invisible and undescribed at once (unchanged today), or visible and undescribed (new), but
  never described while invisible — describing a tool the model was never told exists would be
  wasted tokens with no way to prompt a lookup.
* `extra_visible_tools` (used by the eval harness to force a specific tool's full schema into one
  eval case) bypasses both gates together rather than only the visibility gate, since an eval
  exercising a specific tool needs its actual schema, not a truncated summary plus a `SearchTools`
  round trip.
* `SearchTools` searches every registered tool, not just the undescribed ones, since a model
  might want to double-check a fully-described tool's exact schema too, and restricting the
  search surface would just be one more thing to explain.
