# TUI: merge thinking enable/effort pickers into one command

Context: klorb TUI (Python, Textual-based command palette), feature request.

`ThinkingCommandProvider` (`klorb/src/klorb/tui/commands/thinking_commands.py`) currently
exposes two separate command-palette entries: "Enable/Disable thinking" and "Set thinking
effort". Merge these into a single command that presents one Off/Low/Medium/High choice.

This should mirror the VS Code plugin's already-merged thinking chip / `klorb.setThinking`
QuickPick — see docs/adrs/00162-merge-thinking-enabled-and-effort-into-one-picker.md for the
decision record behind that VS Code implementation, which this TUI change should follow for
consistency between the two frontends.

Existing tests to update: `klorb/tests/klorb/tui/commands/test_thinking_commands.py`.
