# Use Textual's built-in command palette for model selection

* Question: How should the REPL let the user switch which model their prompts are sent to?
* Answer: Register a `klorb.tui.model_commands.ModelCommandProvider`, a Textual
  `command.Provider`, in `ReplApp.COMMANDS`, rather than building a bespoke model-picker
  widget/modal.
* Reasoning: Textual ships a fuzzy-searchable command palette (`Ctrl+P` by default) with
  its own modal screen, list rendering, and keyboard navigation already built and tested.
  Implementing `Provider.search()`/`Provider.discover()` to list models from
  [[model-framework]]'s `ModelRegistry` gets a working, discoverable "switch model" UI for
  a few lines of code, and any other command we add later (slash commands, etc.) can reuse
  the same mechanism instead of accumulating one-off modals per feature.
