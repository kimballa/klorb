# Use Textual for klorb's terminal UI

* Date: 2026-06-29 23:00
* Question: What library should klorb use to build its interactive terminal REPL?
* Answer: [Textual](https://github.com/Textualize/textual), the widget-based TUI framework
  built on top of Rich.
* Reasoning: klorb's REPL needs a scrolling conversation history with an input box pinned to
  the bottom, rich rendering of model output (markdown, syntax highlighting), and the ability
  to keep the UI responsive while a prompt is in flight to a model API. Textual provides all
  of this directly: a widget/layout system (`VerticalScroll`, `Input`, `Markdown`, CSS-based
  styling), an async event loop with first-class support for running blocking work in a
  background thread (`@work(thread=True)` / `call_from_thread`), and a built-in testing
  harness (`App.run_test()` / `Pilot`) for driving the UI from unit tests without a real
  terminal. Lower-level alternatives like `prompt_toolkit` or `urwid` would require building
  most of this layout/scrolling/threading machinery by hand. Textual is already built on
  Rich, so no separate rendering library is needed.
