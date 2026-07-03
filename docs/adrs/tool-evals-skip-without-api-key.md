# `make evals` skips (exit 0) rather than fails when no OpenRouter API key is configured

* Date: 2026-07-02 18:40
* Question: unlike `make test` (fully offline, mocked `ApiProvider`s only), `klorb/evals/`
  makes real `OpenRouterApiProvider` calls to a real hosted model, which requires
  `OPENROUTER_API_KEY` to be set. What should `make evals`/`run_evals.py` do when that key is
  missing — the common case on a contributor machine or a CI runner that was never given one?
* Answer: `run_evals.py` checks `OpenRouterApiProvider().get_api_key()` (or catches the
  `RuntimeError` it raises) before running any case, prints a one-line explanation that evals
  were skipped for lack of an API key, and exits `0`.
* Reasoning: evals answer a different question than `make test` does — "is this tool's schema
  and description clear enough for a real model to use correctly", not "is this tool's Python
  logic correct" — and are opt-in, cost real API tokens, and depend on network access and
  third-party model behavior that can drift over time. `make test` must keep passing
  unconditionally everywhere (that's the whole point of [[persisted-json-schema-versioning]]-style
  isolation from external state), so evals are deliberately kept out of that path (see
  docs/specs/tool-eval-harness.md) and must never turn a missing credential into a build
  failure — that would make `make evals` actively harmful to wire into any environment that
  doesn't already carry the key, and tempt someone into gating CI on it by accident.
