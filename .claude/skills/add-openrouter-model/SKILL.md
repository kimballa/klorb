---
name: add-openrouter-model
description: Add a new klorb-model JSON resource for a model hosted on OpenRouter (klorb/src/klorb/resources/models/*.json). Use whenever the user asks to add, register, or wire up a new model (e.g. "add a model definition for x/y", "register model z"). Every capability field must be resolved from OpenRouter's live /models API response for that exact model id, never from vendor blog posts, comparison sites, or general knowledge, which are frequently stale, conflicting, or outright wrong.
---

# Adding an OpenRouter model to klorb

A `klorb-model` JSON file (see `docs/specs/model-framework.md`) is a factual record of what
a specific OpenRouter-hosted model *is* — its context window, output limit, and which
capabilities it supports. Those facts belong to OpenRouter, not to marketing copy or
third-party model-listing sites. **Every capability field in the file must be resolved from
OpenRouter's own `/models` API response for that exact model id.** Web search results,
vendor announcement pages, and reseller "pricing & specs" sites routinely disagree with each
other and with the API — treat them as leads for finding the right model id, never as a
source for the JSON's field values.

## 1. Resolve the model against OpenRouter's live API

Fetch the full public listing (no API key required):

```
curl -s https://openrouter.ai/api/v1/models -o models.json
```

Then find the one object whose **`id`** field exactly matches the model you're adding
(e.g. `"xiaomi/mimo-v2.5-pro"`):

```
python3 -c "import json; d=json.load(open('models.json')); print([m for m in d['data'] if m['id']=='xiaomi/mimo-v2.5-pro'])"
```

If direct network access to `openrouter.ai` isn't available in your current environment
(sandboxed tool network policies commonly block it), **ask the user to run the `curl`
above and paste the resulting JSON object back** rather than substituting values sourced
from search results or a model's landing page. Do not write or commit a `klorb-model` file
whose capability fields you have not verified against this API response.

If no object's `id` matches, the model isn't (yet) on OpenRouter under that name — stop and
say so rather than guessing at a plausible-looking id. Match on `id`, not `canonical_slug`
(which often carries a dated suffix, e.g. `xiaomi/mimo-v2.5-pro-20260422`) — `id` is the
identifier klorb's `name` field and the API provider argument must use.

## 2. Map API fields to `klorb-model` JSON fields precisely

| `klorb-model` field                | Comes from                                                                 |
|-------------------------------------|-----------------------------------------------------------------------------|
| `name`                              | the matched entry's `id`, verbatim                                          |
| `capabilities.vision`               | `true` iff `architecture.input_modalities` contains `"image"`               |
| `capabilities.function_calling`     | `true` iff `supported_parameters` contains `"tools"`                        |
| `capabilities.thinking`             | `true` iff `supported_parameters` contains `"reasoning"` or `"include_reasoning"` |
| `capabilities.max_context_window`   | `top_provider.context_length` (prefer this over the top-level `context_length`, which can differ slightly) |
| `capabilities.max_output_tokens`    | `top_provider.max_completion_tokens`                                        |
| `knowledge_cutoff`                  | the entry's own `knowledge_cutoff` field (often `null` — that's a real, verified answer, not a placeholder) |

Two fields the API does not disclose unambiguously — resolve these by judgment, not by
inventing a number:

* `capabilities.thinking_budget_style` (`"effort"` vs `"tokens"`) — OpenRouter's
  `supported_parameters` lists a unified `"reasoning"` control without stating which style
  the provider expects. Follow the same family's existing sibling model (e.g.
  `mimo-v2.5-pro` follows `mimo-v2.5`'s `"effort"`) unless you have a provider doc saying
  otherwise.
* `capabilities.streaming` — not represented in `supported_parameters` at all; every
  packaged klorb model sets this `true` (OpenRouter's chat completion endpoint streams by
  default), so leave it `true` unless you have specific evidence a given model can't stream.

Never copy `pricing.*` values into the file directly (e.g. dollar amounts) —
see `docs/adrs/fetch-model-pricing-live-not-from-json.md`. `pricing`'s presence/shape is
still read as a *signal*, not a value — see `settings.temperature` and `cache_mgmt_style`
below.

## 3. Resolve `settings.temperature` and `cache_mgmt_style` from the same API entry

These two fields describe real provider behavior, not klorb-invented defaults — resolve
them from the matched entry too, rather than copying a value used for a different model.

### `settings.temperature`

Temperature is a real model parameter, not a klorb-invented default applied unconditionally.
Check whether `supported_parameters` contains `"temperature"`:

* **Present** → add `"settings": {"temperature": 0.2}` (klorb's standard operating
  temperature for every model that accepts one).
* **Absent** → omit `temperature` from `settings` entirely (`"settings": {}` if nothing else
  applies). Do not add it "to be safe" — sending a parameter a provider doesn't accept can
  be rejected outright, and OpenRouter reports per-model which parameters it actually
  forwards.

This genuinely varies within a single model family: `moonshotai/kimi-k2.7-code` lists
`temperature` and gets `0.2`, while sibling `moonshotai/kimi-k3` does not and ships
`"settings": {}` — don't assume a family-wide answer, check each model's own
`supported_parameters`.

### `cache_mgmt_style`

This is provider-integration behavior, not something klorb invents, but it also isn't a
single flag in the API response — resolve it in two steps:

1. **Does the model support caching at all?** Look at `pricing`: if it has no
   `input_cache_read` key (or `input_cache_read` is `"0"`), the provider offers no caching
   discount for this model — use `"AUTOMATIC"` (no special request handling; there's nothing
   to opt into).
2. **If it does, does the provider require the caller to opt in via a `cache_control`
   field?** Most providers with cache pricing (OpenAI, DeepSeek, Gemini, xAI/Grok,
   Moonshot/Kimi, Groq) cache automatically with no client action — those still use
   `"AUTOMATIC"` despite having cache pricing. Today, OpenRouter's prompt-caching guide
   (https://openrouter.ai/docs/guides/best-practices/prompt-caching) names only **Anthropic**
   and **Alibaba/Qwen** as requiring explicit `cache_control` from the caller — this is a
   provider fact that can change, so check that guide (or the model's own page on
   openrouter.ai) rather than assuming this list is exhaustive or permanent. Within that
   pair: Anthropic's own models take a single top-level `cache_control` flag →
   `"ANTHROPIC_AUTOMATIC"` (see `claude-sonnet-5.json`); Alibaba/Qwen requires the marker
   repeated on each content block → `"ANTHROPIC_EXPLICIT"` (see `qwen-3.7-plus.json`) — the
   name describes the *shape* of the request (Anthropic's own `cache_control` convention),
   not that Anthropic is the provider. A new provider found to require explicit
   `cache_control` should default to `"ANTHROPIC_EXPLICIT"` unless you have specific
   evidence it collapses to a single top-level flag the way Anthropic's own integration does.

## 4. Fill in the fields that are purely klorb's own choice

Unlike the above, these aren't looked up from the API at all — they're decided by matching
existing convention:

* `family` / `model_version` — tease the OpenRouter `id`'s tier apart from its version
  number (see `Model.family()`'s docstring in `klorb/src/klorb/models/model.py`). A variant
  suffix (`-pro`, `-code`, `:nitro`) becomes part of `family`, not `model_version` — e.g.
  `moonshotai/kimi-k2.7-code` is `family: "kimi-code"`, `model_version: "2.7"`.
* `schema` — always `{"name": "klorb-model", "version": "1.0.0"}`, per
  `docs/specs/persisted-json-schema-versioning.md`.
* There is no `pricing` field at all — cost per token is fetched live by
  `klorb.models.openrouter_pricing.fetch_openrouter_pricing()` on demand, never stored.

## 5. Place and name the file

Write to `klorb/src/klorb/resources/models/<slug>.json`, where `<slug>` is the last
`/`-delimited segment of `name`, colons stripped (e.g. `xiaomi/mimo-v2.5-pro` →
`mimo-v2.5-pro.json`, `openai/gpt-oss-120b:nitro` → `gpt-oss-120b-nitro.json`).
`ModelRegistry` discovers every `*.json` file in this directory automatically (see
`docs/specs/model-framework.md`) — no separate registration or index file to update.

## 6. Update `docs/specs/model-framework.md`

The spec's "klorb ships *N* built-in models as `klorb.resources/models/*.json`" bullet
enumerates every packaged model by name. Add the new model to that list and bump the count
so it stays an accurate, current inventory — check the other names in the list against the
files actually on disk while you're there, since drift here is easy to introduce silently.

## 7. Verify

```
make -C klorb lint typecheck test
make lint_docs
```

(the second only matters if you touched the spec, from the repo root.)

## Worked example: `xiaomi/mimo-v2.5-pro`

* `curl https://openrouter.ai/api/v1/models`, matched on `"id": "xiaomi/mimo-v2.5-pro"`.
* `architecture.input_modalities`/`output_modalities` were both `["text"]` — despite several
  search results and reseller pages describing the model as multimodal (confusing it with
  the omnimodal base `mimo-v2.5`), the API is unambiguous: `vision: false`.
* `top_provider.context_length: 1048576` → `max_context_window: 1048576` (not the
  1,050,000 top-level `context_length`, and not the base model's 1,000,000).
* `top_provider.max_completion_tokens: 131072` → `max_output_tokens: 131072`, resolving
  conflicting third-party claims of 16,384 vs. 131,072.
* `supported_parameters` included `"tools"` and `"reasoning"` → `function_calling: true`,
  `thinking: true`; `thinking_budget_style: "effort"` carried over from sibling `mimo-v2.5`.
* `knowledge_cutoff: null` in the API response → `knowledge_cutoff: null` in the file.
* `supported_parameters` included `"temperature"` → `settings.temperature: 0.2`.
* `pricing` included `input_cache_read` (caching is priced), but Xiaomi isn't Anthropic or
  Alibaba/Qwen, so no client-side `cache_control` is required → `cache_mgmt_style:
  "AUTOMATIC"`, same as sibling `mimo-v2.5`.
* `family: "mimo-pro"`, `model_version: "2.5"` — variant suffix folded into `family`,
  matching the `kimi-k2.7-code` precedent.
