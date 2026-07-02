# Wire tool-calling into Session's turn loop with a bookkeeping tool_defs message and a round-trip cap

* Date: 2026-07-01 16:45
* Question: `ToolRegistry.tool_definitions()` and the `tool_defs`/`tool_use`/`tool_response`
  `Message` roles existed but weren't wired into anything (see the "Out of scope" sections of
  docs/specs/tool-framework.md, docs/specs/session-and-turns.md, and
  docs/specs/message-model.md). Now that `Session` can be given a `ToolRegistry`, how should
  tool definitions actually reach the model, how should a model's tool-call request be
  represented in `Session`'s history and dispatched, and what stops a turn from looping
  forever if the model keeps requesting tool calls?
* Answer:
  * `Session._dispatch_turn` computes `tool_registry.tool_definitions()` fresh every turn and
    passes it as `ApiProvider.send_prompt(tools=...)` — the actual, load-bearing way the model
    is offered tools. Separately, the first time a turn is dispatched with a non-empty
    `tool_registry`, a `role="tool_defs"` `Message` is inserted at the very front of
    `self._messages` (`_ensure_tool_defs_message`) — conceptually right after the system
    prompt. (At the time of this decision the system prompt itself was never a stored
    `Message`; it later gained its own bookkeeping message too, inserted just ahead of
    `tool_defs` — see [[store-system-prompt-as-a-bookkeeping-message]].) That message is
    bookkeeping only: `OpenRouterApiProvider._build_api_messages` drops `tool_defs` (and
    `thinking`) before building the request, since OpenAI's chat API has no such role.
  * A model reply that requests tool calls is stored as `role="tool_use"` (not `"assistant"`)
    with `Message.tool_calls` populated, decided after the fact once
    `_send_and_receive`'s stream completes (`finish_reason`/accumulated `delta.tool_calls`
    aren't known until then). `Session._run_tool_calls` dispatches each requested call via
    `tool_registry.instantiate_tool(name).apply(json.loads(arguments))` and appends one
    `role="tool_response"` `Message` per call (`tool_call_id` set), catching lookup/execution
    failures and feeding `f"Error: {exc}"` back as that call's result instead of failing the
    whole turn. `_dispatch_turn` loops — send, run any requested tools, send again — until a
    plain `"assistant"` reply comes back.
  * `MAX_TOOL_CALL_ROUNDS = 10` caps that loop; exceeding it raises `ToolCallLimitExceeded`,
    which `_dispatch_turn` treats like any other mid-turn failure (`user_message` marked
    `processing_state="error"`).
* Reasoning: Sending `tools=` on every request (rather than only when a `tool_defs` message is
  first inserted) keeps the model always in sync with the registry's current tool set even
  though nothing mutates that set mid-session today — the bookkeeping message exists purely so
  klorb's own history/TUI can show what was offered, not to drive the actual request. Feeding
  a failed tool call's error back to the model (instead of raising) matches how function-calling
  models are normally used: the model can see the error and retry with different arguments or
  explain the problem, rather than the entire turn dying on, say, a `ReadFile` call for a
  nonexistent path. A hard round-trip cap exists because nothing else bounds how long a model
  can keep requesting tools without ever answering — better to fail the turn deterministically
  after `MAX_TOOL_CALL_ROUNDS` than run unbounded. Retagging the reply's `role` only after the
  stream completes (rather than guessing up front) is required by the streaming protocol
  itself: `finish_reason` and the full set of `tool_calls` fragments aren't available until the
  last chunk.
