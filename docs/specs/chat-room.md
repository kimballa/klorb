# Chat room

## Summary

The chat room is a broadcast log every session in a tree (root or subagent) and the user can post
to and read from, distinct from `SendMessage`/`GetMessages`'s point-to-point delivery
(docs/specs/subagents.md's "Agent-to-agent messaging"). `PostChat`/`ReadChat`
(`klorb/src/klorb/tools/chat/`) are the tools agents use; the TUI's subagents panel gains a
synthetic chat row for the user. One `klorb.agents.chat.Channel` is shared tree-wide, held on the
root `Session` (`Session.chat_channel`) and reached by every descendant the same way
`agent_message_queue` is. Reading is asynchronous: a participant's own high-water mark (hwm) only
advances when it actually calls `ReadChat` (or, for the user, posts), and a standing interjection
reminds a session of unread messages on its own turns. `@mention`ing a participant by session id or
`role-address` nickname additionally attempts to actively wake it if it's idle.

## Configuration

Three `ProcessConfig` fields under `tools.chat.*`:

* `chat_max_history` (`tools.chat.maxHistory`, default `2000`) — retained message count before
  the oldest are trimmed. Trimming never renumbers `ChatMessage.seq`, so a hwm computed against
  the pre-trim log stays valid; a participant that was already far behind simply loses access to
  the oldest messages it hadn't read yet.
* `chat_max_read_per_call` (`tools.chat.maxReadPerCall`, default `200`) — hard per-`ReadChat`-call
  cap, regardless of the caller's own `limit` argument.
* `chat_max_mention_wakes` (`tools.chat.maxMentionWakesPerSession`, default `50`) — the
  runaway-`@mention`-wake-loop guard; see "`@mention` active wake" below.

## Data model

`klorb.agents.chat.Channel`, one instance per session tree:

* `post(sender_id, body, session) -> ChatMessage` — assigns a monotonically increasing `seq`
  (never reused, even across a trim), resolves `body`'s `@mention`s against `session`'s live tree,
  appends, and trims if `chat_max_history` is exceeded. Also advances `sender_id`'s own hwm to the
  posted message, but only if it was already caught up — posting doesn't silently mark an existing
  unread backlog as read.
* `register_participant(participant_id, at_seq=None)` — seeds a fresh hwm, defaulting to the
  channel's *current* sequence value. Every session calls this on its own id at construction/reset
  (`SessionCoreMixin._reset_state()`), so a freshly spawned agent's hwm starts at "now," not the
  beginning of the log. The TUI does the same for the reserved `"user"` participant id
  (`klorb.agents.chat.CHAT_USER_ID`) the first time the user ever opens the chat room — see "TUI
  integration" below.
* `unread_count`/`unread_mention_count(participant_id)` — how many retained messages a participant
  hasn't read yet, and how many of those `@mention` it directly.
* `read_and_advance(participant_id, limit=None) -> list[ChatMessage]` — the only way (besides
  `post`) a participant's hwm moves forward.
* `history(limit=None) -> list[ChatMessage]` — every retained message regardless of any
  participant's hwm, for a full transcript view.

`ChatMessage` (frozen): `seq`, `sender_id` (a session id, or `CHAT_USER_ID`), `timestamp`, `body`
(raw, never rewritten), `mentions` (resolved participant ids), `unresolved_mentions` (`@token`s
that didn't resolve to any live participant).

### `@mention` grammar and nicknames

`klorb.agents.chat.MENTION_TOKEN_RE` matches a start/whitespace-anchored `@[A-Za-z0-9_.-]+` token,
scoped only to `PostChat`'s `message` argument and the TUI's chat composer — never applied to an
ordinary agent prompt, so it doesn't collide with docs/specs/at-mention-file-inlining.md's
`@foo.txt` syntax. `klorb.agents.chat.live_mention_targets(session)` builds a fresh token ->
canonical id map from `session`'s live tree for one resolution pass: a session's raw id, its
`role-address` nickname, and the literal `"user"`. Both forms resolve uniformly, so an agent can
`@mention` by session id (the same identifier `SendMessage`/the `AgentGroup` interjection already
surface) and a human can `@mention` by the more readable `@role-address` form (e.g.
`@explorer-1.1`, `Session.address()`'s dotted-decimal string).

`klorb.agents.chat.chat_nickname(session_or_id) -> str` maps `"user"` to itself, a live `Session`
to `f"{role}-{address}"`, and any other string (a session no longer in the tree) to itself
unchanged. `ReadChat` returns `body` verbatim; nickname substitution and the `"You"` sender label
are purely a TUI rendering choice (see "Rendering" below), not part of what an agent reads.

## Persistence

`Channel` persists to `sessions/<subdir>/chat.json` (schema `klorb-chat`,
`klorb.workspace.chat_store`), written by `SessionPersistenceMixin.persist_state()` alongside the
rest of a root session's save whenever the channel is dirty, and loaded by `try_restore_session`
into a fresh `Channel.restore()`. The chat log does not survive `/clear`/`reset_session()` — like
`_messages`/`agent_message_queue`, it's conversation-scoped state a reset wipes by rebuilding
`Session.chat_channel` fresh rather than clearing it in place.

## Tools

`PostChat`/`ReadChat` (`klorb/src/klorb/tools/chat/`) are ordinary discovered tools
(`klorb.tools.chat`), both read-only (`is_read_only() == True`) since they mutate harness-managed
shared state, not the user's files or environment. Every role that leaves `restrict_to.tools`
unset inherits both automatically; `explorer`'s narrow allowlist in `resources/agents.json` names
them explicitly.

* **`PostChat(message)`** — posts to the channel as the calling session, then attempts an
  `@mention` active wake (below) for each resolved mention. Result includes `seq`, `mentions`,
  `unresolved_mentions`.
* **`ReadChat(limit=None)`** — returns unread messages oldest-first, capped by
  `chat_max_read_per_call`, advancing the caller's own hwm to the last one returned.
  `remaining_unread` in the result is non-zero only when `limit` cut the batch short.

## Delivery

### Standing "unread chat" interjection

`klorb.agents.chat.build_chat_unread_interjection_provider(session)`, registered in
`_reset_state()` for every session, mirrors `AgentMessageQueue`'s own standing-interjection
mechanism (docs/specs/subagents.md): polled on every `send_turn()`/tool-call round, it emits a
`SystemInterjection subject="ChatUnread"` whenever `unread_count(session.id) > 0`, appending a
second sentence when `unread_mention_count` is also non-zero. A session naturally silences its own
interjection by calling `ReadChat`.

### `@mention` active wake

`klorb.agents.policy.notify_chat_mention(process_config, channel, mentioner, mentioned_id)`, called
once per resolved mention from `PostChatTool.apply()` (skipping self-mentions), reuses
`SendMessage`'s own three delivery branches: a mentioned session with a turn in flight gets nothing
further (the standing interjection covers it next poll); an idle root is woken via
`deliver_event_message`; a dormant subagent is resumed via `dispatch_subagent_turn`, bounded by the
usual `maxConcurrentPerParent`/`maxActiveTotal` limits and simply skipped (not queued) if exceeded.
The wake's delivered text is a fixed nudge ("You were @mentioned in the chat room. Call ReadChat to
see the conversation."), never the message body itself — `ReadChat` is the only thing that ever
advances a hwm, so a woken agent can't "see" content its own cursor doesn't reflect having read.

`Channel._mention_wake_count` (an `AtomicCounter`) increments once per actual wake attempted, not
per mention parsed. Once it reaches `chat_max_mention_wakes`, `notify_chat_mention` stops
attempting further wakes for the rest of the tree's lifetime (logged at `warning`) — `PostChat`
keeps working and the standing interjection keeps working normally, so the room degrades to
passive-only rather than breaking. This guards against two agents `@mention`ing each other back on
every reply, a failure mode distinct from `max_chained_hook_turns` since it spans independent turns
on two different sessions.

The user cannot be actively woken this way — `CHAT_USER_ID` has no turn or thread to resume;
`@user` mentions instead drive the TUI's own attention marker (below).

## TUI integration

### Subagents panel: a third selectable state

`klorb.tui.mixins.subagents_panel.SubagentsPanelMixin` extends the existing Ctrl+G subagents panel
(docs/specs/subagents.md's "Subagents panel (TUI)") with a synthetic "💬 Chat Room" row
(`klorb.tui.constants.CHAT_ROW_ID`), always first in the `OptionList`, ahead of the live session
rows. `ReplApp._chat_selected: bool` is a state layered on top of the existing
`_selected_session`/`_selected_handle` pair rather than folded into it: selecting the chat room
leaves `_selected_session`/`_selected_handle` pointed at whichever (sub)agent was last chosen, and
every render/routing branch that used to check `_selected_handle is not None` now checks
`_chat_selected` first. `SubagentsPanelMixin._select_chat()`/`_select_session()` both save/restore
unsent prompt-input draft text (`ReplApp._subagent_drafts`, keyed by `CHAT_ROW_ID` for the chat
room) the same way switching between agents already does.

A dedicated `#chat-history` `VerticalScroll` (parallel to `#history`/`#subagent-history`) holds the
transcript; selecting the chat row hides the other two and renders a fresh snapshot from
`Channel.history()`, then a per-tick catch-up (`_append_new_chat_messages`) mounts only newly
posted messages while it stays selected, mirroring `#subagent-history`'s own incremental-append
pattern but without a virtualizer (the chat log is capped at `chat_max_history`, not expected to
need one). `_chat_history_pinned_to_bottom` (kept in sync by `_on_chat_history_scroll_changed`)
follows the same pinned-to-bottom convention as the other two transcripts.

### Rendering

Each message renders as `[HH:MM] <sender>: <body>`, built as a `rich.text.Text` (never a raw
markup string, since `body` is arbitrary user/agent-authored text). The sender label is `"You"`
for the user's own posts, else `chat_nickname()` of whichever live session (if any) the sender id
still names. Every `@mention` token inside `body` is re-resolved fresh against the live tree at
render time (`live_mention_targets`, not the `mentions` list `ChatMessage` stored at post time) and
substituted with its own display nickname, rendered in bold — an unresolved or no-longer-live
mention is left as the original raw token, unstyled. The user's own messages get a distinct
`.chat-message-own` style.

### Composing and posting as the user

When the chat row is selected, `PromptSubmissionMixin.on_prompt_input_submitted` routes Enter
straight to `SubagentsPanelMixin._submit_chat_post` instead of dispatching a model turn or a
subagent message: a synchronous `Channel.post(CHAT_USER_ID, text, session)`, mirroring how direct
user-to-subagent messaging (docs/specs/subagents.md's "Direct user messaging") is host-side
dispatch rather than a tool call. The composer accepts `@role-address` nicknames directly, since
`Channel`'s mention parser already resolves that form.

### Attention/unread indicator

`SubagentsPanelMixin._current_chat_marker()` computes the chat row's own marker independently of
the existing `_attention_needed` ask-blink mechanism, since it needs two distinguishable states
rather than one: `"unread"` (a steady `!`) when `unread_count(CHAT_USER_ID) > 0`, upgraded to
`"mention"` (the same blinking `!` the ask-attention marker already uses) when
`unread_mention_count(CHAT_USER_ID) > 0`; `"none"` whenever the chat room is the current selection.
Separately, `_sync_chat_attention()` keeps a synthetic `"chat"` key in `_attention_needed` itself in
sync with the same condition, so `_update_subagent_attention_status_line`'s existing "pick the
oldest pending entry" status-line fallback (shown only while the panel is hidden) picks it up like
any other pending ask, reporting "Chat room has new messages" or "Chat room: you were mentioned."
`_tick_subagents_panel` runs this sync every tick regardless of panel visibility, since an agent
posting to chat while the panel is closed has no other event to notify the TUI with.

### Keybinding

`Ctrl+B` (`action_open_chat_room`) opens the panel if hidden and selects the chat row in one step.
Not `Ctrl+R`: `PromptInput` already binds `Ctrl+R` to a readline-style reverse-incremental-search
over prompt history, unconditionally swallowing the keystroke (`event.stop()`/`prevent_default()`)
whenever the input has focus, so it was never actually free.

## Out of scope

* **VS Code plugin / ACP rendering of the chat room.** The tools work identically over ACP the
  moment they exist (any client can call them), but a chat view in the webview needs its own ACP
  extension methods/notifications and webview messaging — see `TODO.md`.
* **Desktop/OS-level notification** (terminal bell, `notify-send`) on an `@user` mention.
* **Chat message editing/deletion.**
* **Scrolling back past a participant's hwm window** once `chat_max_history` trims it away — no
  `SearchChat`-style tool exists today.
