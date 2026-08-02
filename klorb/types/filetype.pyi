# © Copyright 2026 Aaron Kimball
"""Minimal local type stub for the `filetype` package (PyPI: filetype), which ships no
`py.typed` marker and has no `types-filetype` stub package published. Covers only the
`guess()`/`Type.mime` surface `klorb.session.mixins.mentions` uses."""

from pathlib import Path
from typing import IO

class Type:
    mime: str
    extension: str

def guess(obj: str | Path | bytes | bytearray | IO[bytes]) -> Type | None: ...
