# © Copyright 2026 Aaron Kimball
"""Shared, subject-agnostic mechanics behind the `ReadFile`/`EditFile` and
`ReadScratchpad`/`EditScratchpad` tool pairs: each pair's `Tool` subclass holds one of
`ReadFileCore`/`EditFileCore` as a member (`self.read_file_core`/`self.edit_file_core`) and
delegates nearly all of its `apply()` logic to it, so the line-range read/substitution mechanic
itself is written and tested once. What differs between a file tool and its scratchpad
counterpart — resolving the target `Path` (a model-supplied `filename` checked against
`readDirs`/`writeDirs`, vs. the fixed, harness-managed `Session.scratchpad.path` with no
permission check at all), and a couple of description strings — stays in each `Tool` subclass;
everything else (argument validation, the line-range read or drift-tolerant substitution
algorithm, and building the result dict) lives in this package: `read_file_core.py`
(`ReadFileCore`) and `edit_file_core.py` (`EditFileCore`, `LineRangeEdit`), re-exported here so
callers use `from klorb.tools.util import ReadFileCore, EditFileCore` regardless of which
submodule actually defines them.

Unlike `klorb.tools.scratchpad`, this package's `__init__.py` re-exporting its classes creates
no import cycle: neither `ReadFileCore` nor `EditFileCore` takes a `ToolSetupContext` or
otherwise imports `klorb.tools.setup_context`/`klorb.session` — each is constructed from a
plain `int` and operates on a `pathlib.Path` its caller already resolved.
"""

from klorb.tools.util.edit_file_core import EditFileCore, LineRangeEdit
from klorb.tools.util.read_file_core import ReadFileCore

__all__ = ["EditFileCore", "LineRangeEdit", "ReadFileCore"]
