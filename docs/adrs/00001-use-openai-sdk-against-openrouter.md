# Use the OpenAI SDK, pointed at OpenRouter, as the model client

* Date: 2026-06-29 23:00
* Question: How should klorb send prompts to LLMs and get responses back?
* Answer: Use the `openai` Python SDK, configured with `base_url` set to OpenRouter's
  `https://openrouter.ai/api/v1` endpoint and an `OPENROUTER_API_KEY`, rather than a
  bespoke HTTP client or a separate SDK per model provider.
* Reasoning: OpenRouter exposes an OpenAI-compatible Chat Completions API and proxies to
  many providers/models behind one API key and one billing relationship. The OpenAI SDK is
  mature, typed, and already the de facto standard client for this API shape, so reusing it
  avoids writing and maintaining our own HTTP/retry/streaming logic while still giving
  access to many models through a single integration.
