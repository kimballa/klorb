# Fetch model pricing live from OpenRouter, not from a model's JSON file

* Date: 2026-07-12 12:00
* Question: A `klorb-model` JSON file's `capabilities`, `family`, and `model_version` are all
  effectively fixed facts about a model — they don't change without the model itself becoming a
  different model. Published per-token cost isn't like that: a provider can reprice a model,
  end a promotional rate, or change currency conversion at any time, with no corresponding
  change to the model itself. Should pricing be captured as a static field in each model's
  JSON file the same way, or handled differently?
* Answer: Pricing is never stored in a `klorb-model` JSON file. `klorb.models.
  openrouter_pricing.fetch_openrouter_pricing(model_name)` queries OpenRouter's public
  `GET /models` listing live, each time it's needed, converting OpenRouter's
  dollars-per-token figures into dollars-per-million-tokens (`ModelPricing`). The only caller
  today is `klorb.tui.model_info_commands`'s `"Show model info"` command, which awaits the
  fetch (`asyncio.to_thread`, so it doesn't block the Textual event loop) before rendering the
  modal, and shows `"(not available)"` if the request fails, times out, or the model isn't
  listed. `Model` has no `pricing()` method.
* Reasoning: A number baked into a packaged JSON file only stays correct until the next
  repricing, at which point it's silently wrong until someone notices and ships an update —
  exactly the kind of staleness [[persisted-json-schema-versioning]]'s envelope convention
  exists to let klorb *detect*, but a plain wrong dollar figure has no schema-version marker to
  signal it's gone stale. A live lookup is correct by construction: it reflects OpenRouter's
  pricing at the moment a user actually asks to see it, with no ongoing maintenance burden to
  keep a checked-in number in sync with an external service neither klorb nor the model itself
  controls. The cost of this is a network dependency at display time, mitigated by making the
  one call site (`"Show model info"`) tolerate failure gracefully rather than by trying to
  cache a stale number as a fallback, which would reintroduce the exact staleness problem this
  decision avoids.
