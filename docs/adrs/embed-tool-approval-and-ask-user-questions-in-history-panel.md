# Tool approval and `AskUserQuestions` render as a full-width history panel, not a floating modal

* Date: 2026-07-11 19:30
* Question: `PermissionAskScreen`/`AskUserQuestionsScreen` were both `ModalScreen`s: a
  centered, bordered box (`max-width: 84`, `align: center middle`) floating over the history
  scroll and input box. This had three problems in practice. First, the box was narrower than
  the terminal for no reason other than "that's how a modal looks," squeezing command previews
  and question text into a column well short of the available width. Second, a `ModalScreen`
  suspends the rest of the UI: the history behind it can't be scrolled to reread earlier
  context while deciding. Third, once dismissed, the modal left no trace — the history scroll
  showed the tool call that triggered the prompt and, eventually, its result, but nothing about
  the prompt itself or what the user chose, so scrolling back later couldn't answer "why did I
  approve that?" How should tool approval and `AskUserQuestions` be presented instead?
* Answer: Both are now plain `Vertical` widgets (`PermissionAskPanel`, `AskUserQuestionsPanel`
  — renamed from `...Screen` to reflect that neither is a `Screen` anymore) mounted into a
  persistent, always-present `#interaction-panel` container `ReplApp` composes between the
  history scroll and the prompt input, rather than pushed onto the screen stack. The container
  is empty (and so, with `height: auto`, invisible) outside of an active ask; each panel widget
  carries its own `border-top` and padding, so it reads as a distinct band across the full
  terminal width — not a floating box — the moment one is mounted. While a panel is active,
  `ReplApp._enter_interaction_mode()` disables the prompt input, adds an `interaction-active`
  CSS class that mutes its color and collapses it to `height: 1` (shrinking any in-progress
  multi-line draft back to its default single-row size without discarding the draft text
  underneath, which reappears once `_exit_interaction_mode()` removes the class), and the panel
  itself claims focus so its own key bindings (arrow-key grid navigation, Enter, Escape, `O`,
  `+`) work exactly as they did as a `ModalScreen` — see `PermissionAskPanel`/
  `AskUserQuestionsPanel`'s docstrings for the mechanics. Once a panel reports its decision
  (`dismiss()`, now just an `on_dismiss` callback rather than `ModalScreen`'s built-in
  mechanism, resolving an `asyncio.Future` `ReplApp` is awaiting), `ReplApp` unmounts it and
  mounts a small permanent record into the history scroll instead: the header line (e.g.
  `"Permission requested: Run command"` or `"Question 2 of 3 · Auth"`), the command/path/
  question body, and `"Decision: ..."` — see `ReplApp._record_interaction_history`. The `+`/
  `[more...]` full-screen expansion of a long command (`ExpandedCommandScreen`) is unchanged:
  it's still genuinely modal (a scrollable, read-only overlay with nothing to interact with
  underneath), so there was no reason to move it too.
* Reasoning: A full-width band in the same scrolling document as the rest of the conversation
  solves all three problems at once. Width: the panel's command/detail text can now soft-wrap
  at (approximately) the terminal's own width instead of a fixed 84-column box, which is also
  what makes the accompanying wrap-width-aware command-preview truncation
  (`PermissionAskPanel`'s `preview_wrap_width` — see `_command_preview`'s docstring) meaningful
  in the first place: truncating to a *rendered* line budget only makes sense once the preview
  is actually rendered at close to the real width. Scrollability: since it's an ordinary
  document flow, not a suspended overlay, the history above it is exactly as (Pilot-)scrollable
  during the ask as at any other time, satisfying the explicit "let me reread earlier context
  before deciding" requirement. Permanent record: mounting a plain `Static` group into the same
  `VerticalScroll` the rest of the conversation lives in gets this for free — it's just more
  history content, not a separate mechanism — and is symmetric with how a finished tool call
  already leaves a `<Tool use>` record behind (`ReplApp._mount_tool_call_widget`) rather than
  vanishing once it's done.

  Rejected: keeping the `ModalScreen` but simply widening it to the full terminal width. This
  would have fixed the width complaint alone, but a `ModalScreen` still suspends `Screen`
  input/scroll handling for everything underneath by design (that's the whole point of a
  Textual `ModalScreen`), so the scrollability and permanent-record problems would remain.
  Converting to a plain widget mounted alongside the rest of the document was the only way to
  get an ordinarily-scrollable history back while the ask is up.
