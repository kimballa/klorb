# Exclude thinking-role messages from outgoing API requests

* Date: 2026-06-30 00:00
* Question: `Session` now records streamed reasoning/thinking content as its own
  `Message` (`role="thinking"`) in `self._messages`, interleaved with the `"user"`/
  `"assistant"` messages that make up the conversation. `OpenRouterApiProvider.
  _build_api_messages()` converts the full history into the OpenAI-compatible request body
  on every turn — should `"thinking"`-role messages be included in that request?
* Answer: No. `_build_api_messages()` filters out any `message.role == "thinking"` entry
  before building the OpenAI SDK message list; only `"thinking"` is special-cased this way,
  every other role passes through unchanged.
* Reasoning: `"thinking"` isn't a role the OpenAI-compatible chat completions API accepts
  (only `"system"`/`"user"`/`"assistant"`/`"tool"` are valid) — sending it as-is would be
  rejected by the API. Past reasoning also isn't meant to be replayed as ordinary
  conversation content: it's ephemeral, turn-scoped commentary the model produced en route
  to its actual answer, not a statement either party is expected to stand behind on later
  turns. Keeping `"thinking"` messages in `Session._messages` (rather than discarding them
  entirely) still lets the REPL render them and lets the session log capture them for
  debugging, without requiring them to hold water as replayable dialogue.
