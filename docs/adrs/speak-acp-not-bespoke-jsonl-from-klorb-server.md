# Speak the Agent Client Protocol (ACP) from `klorb server`, replacing the bespoke JSONL stub

* Date: 2026-07-24 00:00
* Question: `klorb server` (`klorb.server.jsonl_server.JsonlServer`) spoke a bespoke,
  two-command JSONL protocol (`{"greet": "..."}` / `{"action": "shutdown"}`) that was a proof
  of concept for "klorb as a persistent process another program drives," not a real wire
  contract. The VS Code plugin needs the real thing: streamed response/thinking text, tool-call
  display, permission approvals, `AskUserQuestions`, task tracking, model/thinking controls —
  everything the interactive TUI already expresses through `Session`'s `TurnEventHandlers`.
  Should `klorb server` grow its own bespoke JSON-RPC-shaped protocol purpose-built for these
  needs, or adopt an existing open protocol, and if the latter, hand-rolled or via an official
  SDK?
* Answer: `klorb server` speaks the [Agent Client Protocol](https://agentclientprotocol.com)
  (ACP) — JSON-RPC 2.0 over newline-delimited stdio — via the official Python SDK,
  `agent-client-protocol` (import name `acp`), pinned `>= 0.7.0, < 0.8.0` (resolves to 0.7.1 at
  the time of writing; pre-1.0, so the minor is pinned and each increment that leans on new SDK
  surface re-verifies it against the pinned version). `klorb.server.acp_server.AcpServer`
  replaces `JsonlServer` outright, with no compatibility shim for the old protocol — a client
  still speaking the JSONL stub just gets the SDK's standard JSON-RPC error replies.
  `klorb.server.klorb_agent.KlorbAcpAgent` implements the ACP `Agent` protocol methods
  (`initialize`, `session/new`, `session/prompt`, `session/cancel` at this checkpoint);
  `klorb.server.turn_bridge.TurnBridge` bridges `Session.send_turn()`'s synchronous,
  callback-driven turn loop onto the SDK's `asyncio` connection. Custom klorb data rides only in
  ACP's own `_meta` extensibility field, under a single `"klorb"` key, and custom methods (once
  any exist) are namespaced `_klorb/<camelCaseName>` — ACP's own sanctioned extension
  mechanisms, not new bespoke wire surface.
* Reasoning: ACP already expresses nearly everything `TurnEventHandlers` does — streamed
  message/thinking chunks, tool-call started/updated events, permission asks, session modes,
  cancellation — so building the VS Code integration on it means the server *and* the parts of
  the client that use standard ACP become drivable by other ACP-aware tooling (Zed, editor
  plugins) for free, and the client-side implementation work is "translate ACP to a webview
  message protocol" rather than "invent and maintain a whole IDE-integration wire format."
  Hand-rolling JSON-RPC framing, request/response correlation, and the ACP type surface by hand
  would duplicate what the official SDKs (`agent-client-protocol` for Python,
  `@agentclientprotocol/sdk` for TypeScript on the client side) already provide as generated
  pydantic/TypeScript models plus connection plumbing, for no benefit — klorb has no reason to
  distrust or diverge from the reference implementation of a protocol it didn't design. The few
  corners ACP doesn't cover (`AskUserQuestions`'s multi-question/header/options shape,
  tool-call-limit escalation, mid-turn message queueing) are deliberately kept off the standard
  surface and pushed into ACP's own extension mechanism instead of stretching a standard method
  to fit or forking the protocol — see the plan overview's "Extensibility rules" section. No
  compatibility shim for the erased JSONL protocol: it was never more than a stub exercised by
  the (also-stub) VS Code plugin, so there was no real client to keep working during the
  transition, and a shim would only have obscured the wire-level cutover this ADR records.
