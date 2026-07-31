# © Copyright 2026 Aaron Kimball
"""Data files shipped inside the installed klorb package, read at runtime via
`importlib.resources` (see `klorb.system_prompt.resolve_prompt_file`). Declared as package
data in `pyproject.toml` so they're present in the wheel. Almost all non-Python; the one
exception, `relay_stub.py`, is packaged Python source read as opaque text rather than imported
as a module -- see `klorb.sandbox.network`."""
