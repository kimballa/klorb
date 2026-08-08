# Use openai/gpt-oss-120b:nitro, not gpt-oss-safeguard-20b:nitro, for the bash safety classifier

* Date: 2026-07-12 15:00
* Question: `openai/gpt-oss-safeguard-20b:nitro` was the packaged model declaring
  `klorb_capabilities.BASH_SAFETY_EVAL` (see
  [[pick-models-for-klorb-owned-tasks-by-capability-flag]]) — the model
  `klorb.permissions.risk_classifier._default_classifier_model` picks by default to classify
  bash command risk. Should klorb keep shipping that model as the built-in
  `BASH_SAFETY_EVAL` bearer, or swap in a different one?
* Answer: Remove the packaged `gpt-oss-safeguard-20b:nitro` model entirely and ship
  `openai/gpt-oss-120b:nitro` in its place (`klorb.resources/models/gpt-oss-120b-nitro.json`),
  moving the `klorb_capabilities.BASH_SAFETY_EVAL` tag onto it. No code in
  `klorb.permissions.risk_classifier` or `klorb.models.registry` changes — `find_by_capability`
  already looks the tag up by content, not by a hardcoded model name, exactly as
  [[pick-models-for-klorb-owned-tasks-by-capability-flag]] intended.
* Reasoning: `gpt-oss-120b:nitro` is OpenAI's larger open-weight reasoning model, giving the
  bash risk classifier more headroom for judgment calls than the 20B-parameter safeguard
  variant while keeping the same `:nitro` (OpenRouter-hosted, throughput-optimized) routing
  and harmony-style effort-based thinking budget. Swapping the tag is a one-line-JSON-file
  change precisely because the capability flag lives on model data rather than being
  hardcoded — the scenario the original ADR anticipated.
