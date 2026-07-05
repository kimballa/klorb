# Share one generic `ConfirmScreen` across every workspace-trust yes/no prompt

* Date: 2026-07-05 06:00
* Question: Workspace bootstrap and the "Trust workspace" command (see
  docs/specs/projects-and-trust.md) between them need four distinct yes/no confirmations
  ("Open as a project?", "Do you trust this workspace?", "Trust workspace" itself, and
  "Initialize the project config file?"). `klorb.tui.repl.ToolCallLimitScreen` already
  establishes the `ModalScreen[bool]`-with-Yes/No-`Button`s shape for exactly this kind of
  prompt — should each of the four get its own near-identical `ModalScreen` subclass (matching
  `ToolCallLimitScreen`'s own precedent of one bespoke screen per prompt), or should they share
  one generic, parameterized screen?
* Answer: A new, generic `klorb.tui.confirm_screen.ConfirmScreen(ModalScreen[bool])` — taking
  `message`, and optional `yes_label`/`no_label` overrides — used for all four. `ToolCallLimitScreen`
  itself is left as its own, separate class; this doesn't refactor it or its call site.
* Reasoning: `ToolCallLimitScreen` predates this feature and already has its own tests and a
  TODO.md-tracked bug (arrow-key navigation between its Yes/No buttons) that names it
  specifically — refactoring it to share `ConfirmScreen` would be an unrelated change bundled
  into this one, and CLAUDE.md is explicit that a feature change shouldn't carry unrelated
  cleanup along with it. But writing four *new*, near-identical `ModalScreen` subclasses for
  this feature alone — differing only in their message text and (for one of them) button
  labels — would be the kind of copy-pasted boilerplate the "reuse" review pass is meant to
  catch, and would only grow harder to justify as more workspace-trust-style prompts are added
  later. A single parameterized screen keeps the CSS, focus-on-mount, and
  button-press/Escape-dismissal logic in one place, and is more testable in isolation.
