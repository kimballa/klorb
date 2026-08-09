# © Copyright 2026 Aaron Kimball
"""Hooks and events: policy overlays that run at lifecycle moments (`hooks`) or in response to
externally-triggered occurrences (`events`), each configured as a `bash`, `classifier`, or
`chat` handler in a `klorb-config.json` layer's `hooks`/`events` object. See `config.py` for
the config-schema pydantic models, `filters.py` for `evaluate_filter`, `merge.py` for the
named-list-concatenate merge used to combine handler lists across config layers, `wire.py` for
the `HookInput`/`HookOutput`/`EventInput` JSON schema a handler is invoked with,
`bash_handler.py` for the `type=bash` handler, and `dispatcher.py` for `HookDispatcher`, which
resolves and runs a hook's configured handler chain.
"""
