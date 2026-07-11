# © Copyright 2026 Aaron Kimball
"""AskUserQuestions: the tool a model uses to pose one or more structured questions to the
user instead of guessing — see `klorb.tools.ask.common` and docs/specs/ask-user-questions.md.

Deliberately does not import this subpackage's `Tool` subclass here, for the same reason
`klorb.tools.scratchpad`'s own `__init__.py` doesn't (see its docstring): `ToolRegistry`
discovers it itself by walking this subpackage's modules directly (see
`klorb.tools.registry.ToolRegistry._discover_tools`), and importing it into this `__init__.py`
would only reintroduce the import cycle that pattern is designed to avoid (`klorb.tools.ask.
ask_user_questions` imports `klorb.tools.setup_context`, which imports `klorb.session` for
real).
"""
