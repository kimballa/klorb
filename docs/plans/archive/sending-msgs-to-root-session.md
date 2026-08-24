# Waking an idle root session from SendMessage

## Context

While implementing the `SendMessage`/`GetMessages` agent-to-agent messaging rework
(see `docs/specs/subagents.md`'s "Agent-to-agent messaging" section), the implementation plan
called "waking an idle root session" out of scope, on the reasoning that actively starting a
turn for a root session (rather than a dormant subagent) would need new work to solve the
threading hop, the UI/streaming wiring, and pushing an unprompted turn over the ACP protocol —
"a much bigger, separate feature."

The user asked, without spinning up a fresh research/plan cycle, why this isn't as simple as it
looked: klorb already has an `Events`/`HookOutput` mechanism where a handler's `output.message`
can kick off another root session turn, and it seemed like a new "message received while idle"
event type — with a degenerate handler that just packages the message body using the same
preamble already used to fold a subagent's queued messages into its next turn — should cover it.

What follows is the answer given in that conversation, based on re-reading the relevant code
(not a full plan cycle): the "much bigger scope" call was wrong. A working wake-an-idle-root
mechanism already exists and is wired up in all three hosts; what's actually still open is a
short list of smaller integration questions, not a new subsystem.

## The correction

You're right, and I need to walk back what I said in the plan — I under-checked this. There's
already a working "wake an idle root" mechanism; I just didn't trace it far enough during
planning.

### What actually exists today

`Session.deliver_event_message(text)` (`session/mixins/turns.py`) is the thing
`Timer`/`FileSystemModified`/`WorkspaceTrustChanged` hook output already routes through, and
it's genuinely host-agnostic — it doesn't know or care that its caller is a hook:

```python
def deliver_event_message(self, text: str) -> None:
    if self._wake_handler is not None:
        self.enqueue_queued_message(QueuedMessage(message_text=text, origin="event"))
        self.deliver_wake()
        return
    if self.parent is not None:
        self._deliver_event_to_subagent(text)
        return
    if self.current_turn_handlers() is not None:
        self.enqueue_queued_message(QueuedMessage(message_text=text, origin="event"))
        return
    raise ChainedHookMessageUndeliverableError(...)
```

And all three hosts register a real wake handler on their root session at construction:

* TUI (`app.py`): `wake()` posts a `TuiSessionWake`; `on_tui_session_wake` (`prompt_submission.py`)
  calls `self._submit_prompt(next_turn_text)` — the exact same call a human's own Enter keypress
  makes, with full streaming callbacks, guarded by `if self._turn_in_flight: return` so it can't
  race a real user submission.
* ACP server (`klorb_agent.py`): `wake()` schedules `_drain_and_submit_woken_turn`, which calls
  `TurnBridge.run_turn(text)` — a real turn, pushed to the client, same `_turn_in_flight` guard.
* CLI headless: registers a no-op, because `run_one_shot`'s own loop already re-polls the queue
  after every turn on the same thread — registering it just satisfies "a host is present" so
  `deliver_event_message` enqueues instead of raising.

So the threading hop, the UI/streaming wiring, and the ACP push problem — the three things
flagged as "much bigger scope" — are all already solved, because `Timer`/`FileSystemModified`
events had exactly this same shape of problem (arbitrary background thread → feed text into an
idle root's next turn) and someone already built the general solution for it.

### So what was actually meant by "out of scope"?

A different, narrower gap: subagent-*output* delivery to an idle creator (`WaitForSubagent`'s
completion, relayed via the `subject="subagent"` standing interjection) has no wake path — that
interjection only fires while a turn is already polling interjections, and nothing calls
`deliver_event_message`/`deliver_wake` for it. That's the thing the existing "Out of scope" doc
note is actually about, and it's still true. The mistake was conflating it with the
SendMessage-to-root case, which doesn't have that problem at all, since it doesn't need the
interjection mechanism — it can go straight through `deliver_event_message`.

### What the proposal gets right, and the small pieces still open

Funneling this through the Events/`HookOutput.message` surface with a degenerate handler is a
fine way to keep it authored consistently with `Timer`/`FileSystemModified`. It's not strictly
required, though: `deliver_event_message` is just a plain public method, so `SendMessage`'s
idle-root branch could call it directly without inventing a new event type. Either way, what's
actually left to decide/build is much smaller than a "wake root" feature from scratch:

1. **No-wake-handler fallback.** `deliver_event_message` raises
   `ChainedHookMessageUndeliverableError` if `_wake_handler` is `None` and there's no turn in
   flight — possible for a bare `Session()` without a host attached (tests, minimal embeddings).
   `SendMessage` would need to catch that and fall back to the passive `AgentMessageQueue`/
   interjection path already built for other cases, rather than surfacing a hard tool error to
   the sender.
2. **Framing.** `deliver_event_message` enqueues raw `text` with `origin="event"` — no "this is
   from another agent, not the user" wrapping. It would still need to run through
   `format_new_turn_message` first, same as the dormant-subagent case.
3. **It's a separate branch, not a scheduler extension.** Root isn't bounded by
   `maxConcurrentPerParent`/`maxActiveTotal` and has no `SubagentHandle`, so this wouldn't plug
   into `try_wake_next_queued_agent` — it'd just be "if the target has no parent, try
   `deliver_event_message`; that's it," independent of the FIFO/dormant-subagent fairness logic.
4. **Burst behavior.** Worth confirming (not assuming) that several `SendMessage` calls landing
   on an idle root in quick succession fold correctly rather than double-submitting —
   `enqueue_queued_message`'s multi-message folding plus the `_turn_in_flight` guards suggest
   it's fine, since this is exactly what already happens when multiple `Timer`/
   `FileSystemModified` events fire close together, but this hasn't been traced through a
   concrete multi-message case in `drain_next_turn_text` to be certain.

None of that is "a whole new plan cycle" — it's a genuinely small addition riding on
infrastructure that already exists. The original "out of scope because hard" call was wrong; it
should have been "out of scope because unchecked."

## What to build

* Sending a message thru SendMessage to a root session that is idle should use
  Session.deliver_event_message() directly, sending the preamble introducing the content
  as an agent-to-agent message concatenated with the message body.
* ... at the time of its completion, we need to have the child agent able to relay
  its response back to its parent.

### child agent relaying to parent

This is fairly straightforward if the parent is the root: just use the same deliver_event_message().

There is a different case if the parent is itself a subagent.

* If that recipient is alive, then we just sys-interject it and it's done.
* If that recipient is idle, then it needs to be woken up, and this could fail if the parallel capacity
  is not there.
  * In general, this sender will only be sending the response-to-parent as it is going idle. So maybe
    we could explicitly hand the capacity reservation ticket from the child to the parent: we can activate
    it just as the child deactivates and the capacity is balanced (since that child had to be sitting on
    some valid capacity reservation to be running).
  * I think there is maybe a question of "what happens if it thinks it's formulating its response, and
    then one of *its* children responds to it -- a msg that won't be known by the agent as it's writing
    that response, only immediately afterward -- and that would tee up a continuation turn? So maybe that
    means that before doing a continuation turn, we have to lose our lease on the capacity slot and try
    to reacquire it?

But in general, notice that "child hands its output to parent" is a
`SendMessage(sender=child, recipient=parent, body=output)` in disguise. Route it through the
`AgentMessageQueue`/`try_wake_next_queued_agent` machinery already built for that: FIFO-fair wake
for a dormant subagent-parent (fixing the silent-drop), correct `parent_interested` propagation
(already how the scheduler computes it), and falling through to `deliver_event_message` only in the
true-root leaf case, same as regular `SendMessage` would.

### small open piece follow-ups

Regarding the small open pieces:

1. Agree that you should catch ChainedHookMessageUndeliverableError and just enqueue it.
2. Yes, agree it needs the preamble.
3. Agree that the root session is never constrained by concurrent agents capacity; there is
   always room to restart the root agent.
4. This seems like a reasonable test case to run. Just because the test case fails and thus
   there is a race condition that needs to be fixed does not mean this is a bad design though.
   It's the right design and we should fix bugs and make it work, rather than take the
   presence of bugs as evidence that the conceptual design is incorrect.
