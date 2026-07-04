# Give each tool-call round its own thinking/response widgets instead of one shared per turn

* Date: 2026-07-04 09:00
* Question: A turn with tool calls is dispatched as a sequence of rounds inside
  `Session._dispatch_turn()` — each round streams its own thinking/response text, then, if it
  ends in a tool-call request, `Session` dispatches those calls before starting the next
  round's stream (see [[session-and-turns]]). `ReplApp._send_prompt`, however, only calls
  `Session.send_turn()` once per user prompt, and its `response_widget`/`thinking_widget`
  variables were declared once for that whole call, outside any notion of a round boundary —
  so every round's chunks landed in the *same* widget, concatenated with no separator, while
  each round's tool-call widgets mounted fresh below it. Watching a long agentic turn run
  therefore looked like one endlessly growing thinking block with tool calls appearing below
  it, when in `Session`'s actual history each round is a distinct `Message`. A prior commit
  ("Fix history scrolling: pin-to-bottom race and stuck response/thinking widgets", PR #20,
  commit b7b6402) had already patched around a symptom of this: reusing the same widget
  across rounds left it stuck above tool-call
  widgets mounted after it started, so `_move_to_end()` was added to reposition it back to the
  end of the history before every update. Given a user's report of watching what looked like
  one unbroken thinking block span an entire multi-tool-call agentic run, should each round get
  its own thinking/response blocks so the transcript's visual order matches `Session`'s actual
  per-round history, and if so, is the `_move_to_end()` repositioning still needed?
* Answer: `_send_prompt` now tracks a `round_index` counter, incremented every time
  `on_tool_call` fires (dispatch only ever happens between two rounds' streams, never mid-
  stream, so seeing it always marks a round boundary having just passed). `handle_chunk` and
  `handle_thinking_chunk` each compare the round their current widget was created for
  (`response_round`/`thinking_round`) against `round_index`; on a mismatch they reset their
  widget reference and accumulator to `None`/`""` before mounting a fresh widget, rather than
  updating the previous round's. Each round's thinking/response therefore renders as its own
  block, appearing in the history in the order it actually happened, with that round's
  tool-call widgets sitting between one round's blocks and the next's. Because a fresh widget
  is always mounted immediately after the previous round's last tool-call widget (nothing else
  can mount in between — a round's whole stream is consumed before `_run_tool_calls` ever
  runs), a round's response/thinking widget is now *always* the last thing in the history for
  as long as it's still being updated — so `_move_to_end()` never has anything to reposition
  and was deleted, along with its call sites in `_update_response_widget`/
  `_update_thinking_widget`. The pin-to-bottom scroll-follow behavior (`_history_pinned_to_bottom`,
  `_scroll_if_pinned`, `_on_history_scroll_changed`) is unrelated to widget identity and is
  untouched by this change.
* Reasoning: `_move_to_end()` treated the symptom (a stuck widget) rather than the cause (reusing
  one widget across what are, underneath, separate `Message`s); once the cause is fixed, the
  symptom's workaround has nothing left to do; keeping dead repositioning logic around
  would just be a second, redundant way to reach the same correct end position. Splitting the
  blocks also improves the abort feature added by
  [[keep-aborted-turn-content-in-history-tagged-not-discarded]]: previously,
  `response_widget`/`accumulated` passed into `_handle_aborted_response` could contain earlier
  rounds' text concatenated in with the interrupted round's, whereas `Session` only ever tags
  the *interrupted round's own* placeholder `Message` as `"aborted"` — the TUI's displayed
  "(interrupted)" marker now lands on exactly the same round's text `Session` marked, instead of
  appearing to interrupt a block that was mostly composed of earlier, already-completed rounds.
