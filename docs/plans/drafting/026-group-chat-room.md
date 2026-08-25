# Plan 026: Group chat room

## Problem

`TODO.md`'s "Agent / Harness" feature backlog:

> Add a group chatroom. Everyone has PostChat and ReadChat tools. It tracks high watermark
> unread for all agents. New agents have their hwm start at "now" when spawned, not the
> beginning. All chat reading is async. @mentioning an agent that's alive gives it a sys
> injection nudge to read chat. An idle agent is awoken with the chat content. The user can
> read the chatroom in the TUI and also write to it. It looks like IRC. (Or slack, for the
> newfangled thing these days.) Agents can @mention the user by referring to `@user`. You get
> a notification if you're on an agent history screen rather than in the chat.

and, under "TUI" / "Feature backlog":

> chat room with your agents

Today, agent-to-agent communication (docs/specs/subagents.md's "Agent-to-agent messaging") is
strictly point-to-point: `SendMessage`/`GetMessages` deliver one message from one sender to one
named recipient through `AgentMessageQueue`, a tree-wide FIFO that's drained per-recipient and
has no notion of a shared, browsable transcript. There is no surface — for agents or the human —
where every participant in a session tree can see the same running conversation. This plan adds
that surface: a broadcast chat log every agent and the user can post to and read from, with
per-participant unread tracking and `@mention`-driven nudges, plus a TUI view for it.

## Prior art this plan builds on

This is deliberately not a from-scratch subsystem — it's a new delivery *shape* (broadcast +
independent read cursors, instead of point-to-point queued delivery) layered on mechanisms
docs/specs/subagents.md and docs/specs/hooks-and-events.md already established:

* **`klorb.agents.messaging.AgentMessageQueue`** (`klorb/src/klorb/agents/messaging.py`) is the
  closest existing analogue: one instance per session tree, held on the root `Session` and
  reached by every descendant via `.parent` (`get_agent_message_queue`), the same sharing pattern
  `workspace_indexer` uses. A new `klorb.agents.chat.Channel` follows this exact shape — named
  `Channel`, in keeping with the IRC framing TODO itself reaches for ("It looks like IRC"), not
  `ChatRoom`.
* **Standing interjections** (`Session.register_standing_interjection`, polled on every
  `send_turn()` and between tool-call rounds within a turn) are how `AgentMessageQueue` reminds a
  session it has unread messages waiting (`subject="AgentMessage"`) without a live push channel.
  The chat room's own "you have unread messages" nudge reuses this mechanism verbatim, under a new
  `subject="ChatUnread"`.
* **Dormant-subagent and idle-root wake delivery** (`klorb.agents.policy.
  try_wake_next_queued_agent`/`dispatch_direct_message`/`Session.deliver_event_message`) is how a
  message reaches a session that isn't actively turning. `@mention`-driven wakes reuse these same
  three delivery branches (running / idle root / dormant subagent) — see "Delivery" below for how
  the payload differs from `SendMessage`'s.
* **`SubagentsPanel`/`SubagentsPanelMixin`** (docs/specs/subagents.md's "Subagents panel (TUI)")
  already solves "a single global selection decides which transcript is on screen, with a blinking
  attention marker for whichever session needs the user's notice while a different one is
  selected." The chat panel is designed to extend this exact mechanism with one more selectable
  target, rather than building a parallel selection/attention system.

## Scope

### In scope

* Core data model, persistence, and the `PostChat`/`ReadChat` tools — usable from any host
  (headless, TUI, ACP) the moment `ToolRegistry` discovers them, since no host-specific plumbing
  is required for the tools themselves.
* The unread-tracking/nudge mechanism (standing interjection) and the `@mention` active-wake path.
* A TUI chat view: reading the room, composing and posting as the user, and an unread/attention
  indicator when the user isn't looking at it.

### Out of scope

* **Any vscode-plugin/ACP rendering surface.** The tools work identically over ACP the moment
  they're implemented (any client can call them), but a chat *view* in the VS Code webview needs
  its own ACP extension methods/notifications and webview messaging, the same category of work
  `docs/specs/vscode-plugin.md`'s existing tool-call/history plumbing required — see "Future work"
  below. This plan does not assume "port the TUI panel" is sufficient for that; it explicitly
  isn't (see the `vscode-tech-planning` skill's reasoning for why a TUI-first plan that later grows
  a vscode-plugin phase needs a fresh accounting of the wire protocol, not a reused UI sketch).
* Desktop/OS-level notifications (terminal bell, `notify-send`, etc.) on an `@user` mention — no
  such mechanism exists anywhere in klorb today (checked: no bell/notify-send call sites), and
  adding one is an unrelated, standalone feature.
* Chat message editing/deletion, and any content moderation beyond the runaway-wake-loop guard
  described below.

## Data model

New module `klorb/src/klorb/agents/chat.py`, sibling to `klorb/src/klorb/agents/messaging.py`:

* **`ChatMessage`** (frozen dataclass): `seq: int` (a monotonically increasing, never-reused
  sequence number — see "why not a list index" below), `sender_id: str` (a session id, or the
  reserved literal `"user"`), `timestamp: datetime`, `body: str` (the raw text exactly as posted,
  never rewritten), `mentions: list[str]` (participant ids/`"user"` recognized in `body`),
  `unresolved_mentions: list[str]` (an `@token` in `body` that didn't resolve to any live
  participant — left as plain text, not specially rendered, but recorded so `PostChat`'s own
  result can tell the caller a mention didn't land). Deliberately **no** `sender_role`/
  `sender_title` field: a sender's role and display nickname are derivable from `sender_id` alone
  by walking the live session tree at render time (see "Nicknames" below), so nothing needs to be
  captured redundantly at post time.
* **`Channel`**: one instance per session tree, constructed lazily and held on the root
  `Session` (`Session.chat_channel`, mirroring `Session.agent_message_queue`), reached by every
  descendant via the same `.parent`-walk `get_agent_message_queue` already uses. Holds:
  * `_messages: list[ChatMessage]` — the full retained log, oldest first.
  * `_next_seq: klorb.counter.AtomicCounter` — the sequence generator. A bare `int` plus `+=`
    is exactly the "unsynchronized read-modify-write counter" hazard
    docs/specs/threading-audit.md's finding 7 already documents for `Session.
    _allocate_child_index` (two concurrent `PostChat` calls, from two different agents' own
    threads, could otherwise read the same `_next_seq` before either writes it back and produce
    two messages sharing one `seq`) — `AtomicCounter.increment()` is the established fix for
    this exact shape, used instead of introducing a second ad hoc locking pattern.
  * `_hwm: dict[str, int]` — each participant's last-seen `seq` (their high-water mark).
  * `_mention_wake_count: klorb.counter.AtomicCounter` — see "Runaway-wake-loop guard" below;
    same reasoning as `_next_seq` for why this isn't a plain `int`.
  * `_lock: threading.Lock` guarding `_messages`/`_hwm` (the fields `AtomicCounter` doesn't
    already cover on its own), the same granularity `AgentMessageQueue` uses.

  Methods: `post(sender_id, body) -> ChatMessage` (assigns `seq` via `_next_seq.increment()`,
  parses mentions, appends, trims if `tools.chat.maxHistory` is exceeded — see "Config" below);
  `register_participant(participant_id, at_seq=None)` (seeds a fresh hwm entry, defaulting
  `at_seq` to the *current* `_next_seq.get_value()` — this is the literal mechanism behind TODO's
  "new agents have their hwm start at 'now', not the beginning"); `unread_count(participant_id)
  -> int`; `unread_mention_count(participant_id) -> int` (of that same unread set, how many
  `mentions` this participant — see "Standing 'unread chat' interjection" below);
  `read_and_advance(participant_id, limit=None) -> list[ChatMessage]` (the only way a
  participant's own hwm moves forward); `history(limit=None) -> list[ChatMessage]` (the full/tail
  log regardless of any hwm, for the TUI transcript, which always shows everything rather than
  only "unread" content); `participant_ids() -> frozenset[str]`.

### Why a sequence number, not a list index

`_messages` is trimmed from the front once `tools.chat.maxHistory` is exceeded (see "Config"). If
a participant's hwm were a plain list index, trimming would silently invalidate every stored
cursor. `seq` is assigned once, never reused, and never renumbered on trim, so `unread_count`/
`read_and_advance` (`count of retained messages with seq > hwm`) stay correct after a trim — the
only user-visible effect of a trim is that a participant who was already very far behind loses
access to the oldest messages they hadn't read yet, the same acceptable trade-off `GrepTool`'s
`grep_max_results` cap already makes for a single call's result size, just applied to retention
instead.

### Participant identity, `@user`, and nicknames

A "participant" is any session id in the current tree (root or subagent, `walk_session_tree`) plus
the one reserved literal `"user"`, standing in for the human at the TUI/ACP client.

**Two distinct `@mention` surfaces, both resolved against the same live tree snapshot, but
favoring different forms:**

* **Agents author mentions by raw session id** (`@1706040000-blue-otter`) — the same identifier
  `SendMessage`'s own `id` argument already requires and the `AgentGroup` standing interjection's
  `Id` column already surfaces to every agent in the tree (docs/specs/subagents.md), so an agent
  composing a `PostChat` message reaches for an identifier it already has on hand rather than one
  it has to compute.
* **The user authors (and reads) mentions by a role+address nickname** — `@<role>-<address>`, e.g.
  `@explorer-1.1` or `@operator-1` (`address` is `Session.address()`'s own dotted-decimal string,
  docs/specs/subagents.md's "Addressing"). This is unambiguous (an address is unique and stable
  for a session's lifetime) and far more readable than a raw id for a human typing or scanning a
  transcript.

`Channel`'s mention parser accepts **either** form uniformly: split `body` on a start/whitespace-
anchored `@token` grammar (a plain `@[A-Za-z0-9_.-]+` token suffices — a chat mention never needs
the quoting/escaping `at-mention-file-inlining.md`'s own grammar carries for arbitrary filenames),
then resolve each token against a snapshot built fresh from `walk_session_tree` for this one
`post()` call: an exact session-id match, an exact `f"{role_name}-{address}"` nickname match, or
the literal `user`. This is a **separate grammar from `@`-file-mention inlining**, scoped only to
`PostChat`'s own `message` argument and the TUI chat input widget — never applied to the ordinary
agent prompt input, so there is no collision with docs/specs/at-mention-file-inlining.md's
existing `@foo.txt` syntax.

**Rendering always prefers the nickname form for the human**, regardless of which form the
original poster typed: the TUI transcript (see "TUI integration" below) substitutes any resolved
mention token with `@<role>-<address>`, computed fresh at render time from the live tree (never
stored on `ChatMessage`, since a role/address pairing is only meaningful while that session is
still part of the tree — this is also why `sender_id` alone, not a captured `sender_role`/
`sender_title`, is enough on the stored message: the nickname is a view, not stored data). A
message an agent reads via `ReadChat` is returned with `body` verbatim, unrewritten — an agent
already correlates ids via its own `AgentGroup` table and doesn't need the nickname substitution
the human-facing view performs.

`klorb.agents.chat.chat_nickname(session_or_id) -> str` is the one shared helper both the mention
resolver and the TUI renderer call, so the two never compute the mapping differently: `"user"` ->
`"user"`; a live session -> `f"{session.config.role_name}-{session.address()}"`; a session id no
longer present in the tree (a closed process's leftover, or a stale persisted message) -> falls
back to the raw id, since there's no live role/address left to compute from.

## Persistence

`Channel` persists to a new `sessions/<subdir>/chat.json` — schema-versioned per
docs/specs/persisted-json-schema-versioning.md (`{"schema": {"name": "klorb-chat", "version":
"1.0.0"}, "messages": [...], "hwm": {...}, "next_seq": ...}`). Rather than writing on every single
`PostChat` call, `Channel` tracks a dirty flag and `SessionPersistenceMixin.persist_state()` — the
root session's own existing whole-file-rewrite save path (docs/specs/session-persistence.md) —
additionally rewrites `chat.json` when dirty, in the same call. This reuses the existing
session-subdir-claim/lock machinery instead of adding a second, independent write path with its
own atomicity story. On restore (`try_restore_session`), a present `chat.json` seeds a fresh
`Channel`'s `_messages`/`_hwm`/`_next_seq` directly; a restored hwm entry for a session id that
never reappears in the new process (an old subagent) is simply inert — never pruned, but harmless.

**The chat log does not survive `/clear`/`reset_session()`.** It's conversation-scoped state, the
same category `_reset_state()` already wipes for `_messages`/`agent_message_queue`/
`subagent_tracker` and everything else "Session reset" in docs/specs/hooks-and-events.md
enumerates — a fresh conversation starts with a fresh, empty `Channel`, not the prior
conversation's transcript. `Session.chat_channel` is rebuilt (not merely cleared in place) the
same way the rest of `_reset_state()`'s conversation-scoped fields are.

## New tools: `PostChat` / `ReadChat`

New subpackage `klorb/src/klorb/tools/chat/` (`post_chat.py`, `read_chat.py`), following the same
`klorb/src/klorb/tools/subagents/`/`klorb/src/klorb/tools/tasks/` subpackage convention those
tool families already use.

### `PostChat`

Args: `message: str` (required). Requires a live `context.session` (raises `ToolCallError`,
category `"validation"`, otherwise — the same requirement `GrepTool`'s spill path has for a
different reason: there's no meaningful "post to a chat room" without a session identity to post
*as*). `apply()` calls `Channel.post(session.id, message)`, then attempts the `@mention`
active-wake path (below) for each resolved mention. Result: `{"seq", "mentions",
"unresolved_mentions", "note"}`, `note` a fixed reminder that other
agents receive this asynchronously via their own `ReadChat` call, not immediately. `summary()`:
`"Posted to chat room (#<seq>)"`, with a mention count suffix when non-empty. `is_read_only() ==
True` — like `SendMessage`/`EditScratchpad`, this mutates harness-managed shared conversation
state, not the user's files or environment, so it's safe to offer even under
`enforce_readonly_tools`.

### `ReadChat`

Args: `limit: int | None` (optional per-call cap, hard-bounded by `tools.chat.maxReadPerCall`
regardless). `apply()` calls `Channel.read_and_advance(session.id, limit=...)`. Result:
`{"messages": [...], "count", "remaining_unread"}` — `remaining_unread` is non-zero only when
`limit` cut the batch short, telling the caller to call `ReadChat` again rather than assuming
it's caught up. An empty result (`count == 0`) is not an error, mirroring `GetMessages`'s "No new
messages waiting." shape. `is_read_only() == True`.

Both tools ship `default_visible() == True`/`default_described() == True` — each has at most one
argument, no reason to hide either behind `SearchTools` the way `ReplaceAll` is.

## Delivery: the unread interjection and `@mention` wakes

### Standing "unread chat" interjection

`klorb.agents.chat.build_chat_unread_interjection_provider(session)` mirrors
`klorb.agents.messaging`'s own `AgentMessage`-subject provider exactly: registered in
`SessionCoreMixin._reset_state()` (so every session, root or subagent, carries it from
construction), polled on every `send_turn()` call and between tool-call rounds, emitting
`<SystemInterjection subject="ChatUnread">You have N unread chat room message(s). Call ReadChat to
see them.</SystemInterjection>` whenever `Channel.unread_count(session.id) > 0` — no
change-tracking needed beyond that, since a session that calls `ReadChat` naturally silences its
own interjection by draining its unread count to zero, the same self-quieting behavior
`AgentMessage`'s interjection already has. When `Channel.unread_mention_count(session.id)` is also
non-zero, the same interjection appends a second sentence calling that out specifically: `"...
including M that @mention you directly."` — so a session skimming a long unread count still knows
whether any of it was addressed to it by name, without having to call `ReadChat` just to find out.

This alone satisfies TODO's "all chat reading is async" and "new agents have their hwm start at
now" requirements for any agent that's already actively turning, or that turns again later for any
other reason — it will notice unread chat on its own. `@mention` wakes (below) are the *additional*
mechanism for a session that's currently idle and would otherwise have no reason to turn again
soon.

### `@mention` active wake

`PostChatTool.apply()`, after `Channel.post()` returns the resolved `mentions`, calls a new
`klorb.agents.policy.notify_chat_mention(session, chat_channel, mentioned_id)` once per mention
(skipping the poster's own id, if it somehow mentions itself). This reuses the exact same
three-way branch `SendMessage`'s own delivery already has
(docs/specs/subagents.md's "Agent-to-agent messaging"):

* **Mentioned session has a turn in flight** — nothing further happens; the standing interjection
  above will reach it once it polls again (which for an active turn is imminent — the next
  tool-call round).
* **Mentioned session is an idle root** — delivered via the same `deliver_event_message`/
  `deliver_or_queue_agent_message` idle-root wake `SendMessage` and hook/event delivery both use.
* **Mentioned session is a dormant subagent** — resumed via `dispatch_subagent_turn`, the same
  dormant-wake path `try_wake_next_queued_agent`/`dispatch_direct_message` use, bounded by the
  same `maxConcurrentPerParent`/`maxActiveTotal` concurrency limits; if capacity is exceeded, the
  wake is simply skipped (not queued or retried) — the standing interjection is still there the
  next time that subagent naturally turns, so nothing is lost, only the "wake it right now"
  convenience is unavailable under contention.

**Unlike `SendMessage`, the wake's own delivered text is *not* the chat message body.** It's a
short, fixed nudge: `"You were @mentioned in the chat room. Call ReadChat to see the
conversation."` The chat log itself — not this nudge text — is the single source of truth for
what was said, and `ReadChat` is the only thing that ever advances a participant's own hwm. If the
wake inlined the message body directly (the way `format_new_turn_message` does for
`SendMessage`), a woken agent could "see" the mention without its hwm ever moving, decoupling what
it actually read from what its own cursor claims it read. Requiring the explicit `ReadChat` call
keeps that invariant intact at the small cost of one extra tool round trip.

### Runaway-wake-loop guard

Two agents that each `@mention` the other back in every reply could wake each other indefinitely
across turns — a distinct failure mode from the bounded, single-turn `max_chained_hook_turns`
guard (docs/specs/hooks-and-events.md), since this spans independent turns on two different
sessions. `Channel._mention_wake_count` (the `AtomicCounter` from "Data model" above) is
incremented once per actual wake attempted (not per mention parsed — a mention of an
already-running session costs nothing). Once it reaches `tools.chat.maxMentionWakesPerSession`
(see "Config"), `notify_chat_mention`
stops attempting further active wakes for the rest of this tree's lifetime — logged at `warning`
— but `PostChat` itself keeps succeeding and the standing interjection keeps working normally,
so the chat room degrades to "passive only" rather than breaking. This mirrors the "cap it, log a
warning, keep going in a degraded mode" shape `max_chained_hook_turns`/`messaging_max_queue_size`
already use elsewhere in the codebase, rather than introducing a new failure style.

## Tool-set surface

`PostChat`/`ReadChat` are ordinary discovered tools (`ToolRegistry.discover_tools` walks
`klorb.tools.chat` the same way it already walks `klorb.tools.subagents`/`klorb.tools.tasks`).
Every role whose `agents.json` entry leaves `restrict_to.tools` unset (`operator`, `reviewer`,
`implementer`, `pair_programmer` — see docs/specs/subagents.md) inherits both automatically,
matching TODO's "everyone has PostChat and ReadChat tools." `explorer`'s entry, however, sets an
*explicit* narrow `tools` allowlist (`FindFile`, `Grep`, `ListDir`, `ReadFile`, ...) — "everyone"
does not reach it for free, so Phase 1 adds `PostChat`/`ReadChat` to that list explicitly:
Explorer gets chat access too, confirmed rather than left as an open question, even though it's a
real addition to a role whose contract (docs/specs/subagents.md's "Explorer role") is otherwise
silent and report-only — an Explorer can now also post a finding or a question into the shared
room while it works, not only in its final report.

## Config

Three new `PROCESS_KEY_MAP` (top-level, process-scoped) entries under a `tools.chat.*` namespace —
process-scoped for the same reason `tools.subagents.*`/`tools.messaging.*` are: a cross-cutting
resource limit, not a per-session interactive toggle:

* `tools.chat.maxHistory` (`ProcessConfig.chat_max_history`, suggested default `2000`) — retained
  log length before the oldest entries are trimmed (see "Why a sequence number, not a list index"
  above).
* `tools.chat.maxReadPerCall` (`ProcessConfig.chat_max_read_per_call`, suggested default `200`) —
  hard per-`ReadChat`-call cap, mirroring `DEFAULT_GREP_MAX_RESULTS`'s role for `Grep`.
* `tools.chat.maxMentionWakesPerSession` (`ProcessConfig.chat_max_mention_wakes`, suggested
  default `50`) — the runaway-wake-loop guard above.

Each needs a shipped-default entry in `klorb/resources/default-config.json`, per
docs/specs/process-and-session-config.md's "adding a new config-file-exposed setting" checklist.

## TUI integration

### Reusing the Subagents panel's selection mechanism

Rather than building a second, parallel panel/selection/attention-marker system, this plan extends
`SubagentsPanel`/`SubagentsPanelMixin` (Ctrl+G) with one more selectable target. A synthetic
"💬 Chat Room" row is prepended to the panel's `OptionList`, ahead of the live session rows.
Selecting it is a third state layered on top of today's `_selected_session`/`_selected_handle`
pair — the simplest-looking option (least disruptive to the many existing branches keyed off
`_selected_session`) is an additional `ReplApp._chat_selected: bool`, checked ahead of the existing
root-vs-subagent branches wherever they currently decide what to render/route, rather than folding
"chat" into `_selected_session` itself (which isn't a real `Session` and would force every one of
those call sites to add a `None`-but-actually-chat special case instead of one new flag check).
The exact mechanism is left to the implementer to confirm once the real branches are in front of
them, rather than mandated here.

### Rendering

A new `#chat-history` `VerticalScroll` (parallel to `#history`/`#subagent-history`), populated
from `Channel.history()` and rendered IRC/Slack-style: `[HH:MM] <nickname>: <body>`, where
`<nickname>` is `chat_nickname(sender_id)` (`@user` renders as e.g. `"You"`, an agent as its own
`role-address` nickname — see "Participant identity, `@user`, and nicknames" above) and any
`@mention` inside `body` (including `@user`) is substituted with its own nickname form and
rendered in bold, regardless of whether the original poster typed a raw id or a nickname. The
user's own posted messages are styled distinctly from agent posts. Reuses the pinned-to-bottom
scroll tracking pattern
(`_subagent_history_pinned_to_bottom`/`_on_subagent_history_scroll_changed`) `#subagent-history`
already has, under new names scoped to `#chat-history`. Because `Channel` is shared tree-wide and
already lives on the root session, this view needs no per-subagent variant the way the transcript
view does — there is exactly one chat room per tree, matching there being exactly one
`AgentMessageQueue`.

### Composing and posting as the user

When the chat row is selected, the existing prompt input's Enter-key handling routes to
`PostChatTool` directly — a synchronous log append via `Channel.post("user", text)`, not an LLM
turn (there is no model call involved in the user posting to chat, mirroring how a human's direct
message to a subagent, per docs/specs/subagents.md's "Direct user messaging," is host-side
dispatch rather than a tool call — posting to chat is host-side too, just even simpler, since
there's no target session to wake or queue into for the user's own post). The composer accepts
`@role-address` nicknames directly (e.g. typing `@explorer-1.1`); no separate autocomplete-then-
rewrite step is needed, since `Channel`'s mention parser already resolves that exact form (see
"Participant identity, `@user`, and nicknames" above) — a live-roster autocomplete popup as the
user types `@` is a natural, but non-essential, usability nicety layered on top later, not a
prerequisite for the nickname form to work. Posting implicitly counts as "read up to here" for the
poster, matching ordinary chat-client behavior, so `Channel.post` also advances the poster's own
hwm to the message it just wrote.

### Attention/unread indicator

`ReplApp._attention_needed` (today populated only for a subagent panel row whose interactive ask
is waiting on the user) gains a synthetic `"chat"` entry whenever `Channel.unread_count("user") >
0` while `_chat_selected` is `False`. Unlike the subagent-attention case, this needs **two**
distinguishable visual states, not one, since an unread ordinary post and an unread post that
`@user`-mentions the human are different urgency levels:

* **Plain unread** (`unread_count("user") > 0`, `unread_mention_count("user") == 0`) — a
  **steady** `!` marker on the chat row, not blinking. Present, but not demanding attention the
  way the existing subagent-ask marker does.
* **Unread `@mention`** (`unread_mention_count("user") > 0`) — the existing blinking `(!)` marker
  `SubagentsPanel.show_rows`/`_tick_subagents_panel` already renders for a subagent row awaiting
  the user's input, reused as-is: an `@user` mention is exactly the "this needs you specifically"
  signal that marker was already built for.

Both states also drive the `#subagent-attention-status`-style status-line fallback text when the
panel itself is closed (e.g. "Chat room has new messages" vs. "Chat room: you were mentioned"),
mirroring the existing steady-vs-blinking split in the marker itself. This is the literal
mechanism behind TODO's "you get a notification if you're on an agent history screen rather than
in the chat" — reusing, not reimplementing, the same infrastructure that already handles "you're
looking at session X but session Y needs you," extended with one more severity level.

### Keybinding

Recommend adding a direct `Ctrl+R` ("Room") binding that jumps straight to the chat view — opening
the panel and selecting its chat row in one step — in addition to reaching it through Ctrl+G's
row list. TODO frames the chat room as a primary, frequently-used surface ("It looks like IRC...
Slack"), not something that belongs nested inside a panel titled around subagents; a dedicated key
keeps it one keystroke away. `Ctrl+R` is unused today (existing bindings: `ctrl+c`/`ctrl+q`/
`ctrl+o`/`ctrl+t`/`ctrl+g`/`ctrl+e`, per `klorb/src/klorb/tui/app.py`).

## Implementation phases

### Phase 1: Core data model and tools

* `klorb.agents.chat.ChatMessage`/`Channel`/`chat_nickname()`, `Session.chat_channel` (lazily
  constructed, tree-shared, mirroring `agent_message_queue`; rebuilt fresh on
  `reset_session()`/`/clear`, per "Persistence" above).
* `PostChat`/`ReadChat` tools (`klorb/src/klorb/tools/chat/`), unit tests for mention parsing
  (both raw-id and `role-address` nickname forms), hwm advancement, the `maxHistory` trim, and the
  `maxReadPerCall` cap.
* `tools.chat.*` config keys wired into `ProcessConfig`/`default-config.json`.
* Add `PostChat`/`ReadChat` to `resources/agents.json`'s `explorer` entry's `restrict_to.tools`
  allowlist.
* `chat.json` persistence (write on `persist_state()`, load in `try_restore_session`).

### Phase 2: Unread interjection and `@mention` wakes

* `ChatUnread` standing interjection provider, registered in `_reset_state()`.
* `klorb.agents.policy.notify_chat_mention`, wired into `PostChatTool.apply()`, covering all three
  delivery branches (running / idle root / dormant subagent) plus the
  `maxMentionWakesPerSession` guard.
* Tests: a dormant subagent mentioned by name actually resumes; an idle root gets woken; a
  running target gets no active wake, only the standing interjection; the wake counter caps out
  and degrades to passive-only without breaking `PostChat` itself.

### Phase 3: TUI integration

* `#chat-history` rendering, the panel's synthetic chat row, the `_chat_selected` state, and
  prompt-input routing for posting as the user.
* `_attention_needed`'s `"chat"` entry, its steady-vs-blinking marker split, and status-line
  rendering.
* `Ctrl+R` binding.
* A live-roster `@`-autocomplete popup in the chat composer, as a usability nicety on top of the
  `role-address` nickname grammar `PostChat` already accepts as of Phase 1 (not a prerequisite for
  it — see "Composing and posting as the user" above).
* Pilot-based integration tests, following the pattern `klorb/tests/klorb/tui/mixins/
  test_subagents_panel.py` already uses for the analogous subagent-selection mechanics.

### Phase 4: Docs and polish

* New `docs/specs/chat-room.md` (or fold into `docs/specs/subagents.md`, if the implementer judges
  the two are tightly enough coupled to share one spec — the drafting choice is left open) covering
  the shipped data model, tools, config, and TUI mechanics as they actually landed.
* `docs/user/` reference update, if a user-facing docs page already documents `SendMessage`/
  `GetMessages` in a way `PostChat`/`ReadChat` should sit alongside.
* Remove both `TODO.md` entries this plan implements once done.

## Future work

Log these to `TODO.md` under a new `### Plan 026: Group chat room` subsection once this plan is
archived, per `docs/plans/README-PLANS.md`:

* VS Code plugin / ACP rendering of the chat room — needs its own ACP extension methods/
  notifications and webview messaging design, not a reuse of the TUI panel's own mechanics.
* Desktop/OS-level notification (terminal bell, `notify-send`) on an `@user` mention.
* Chat message editing/deletion.
* A `SearchChat`-style tool for scrolling back through history older than a participant's own hwm
  window, if bounded retention (`tools.chat.maxHistory`) turns out to be too aggressive in
  practice for long sessions.
