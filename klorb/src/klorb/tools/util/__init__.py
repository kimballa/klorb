# © Copyright 2026 Aaron Kimball
"""Shared, subject-agnostic mechanics behind the `ReadFile`/`EditFile`/`CreateFile` and
`ReadScratchpad`/`EditScratchpad` tool pairs (plus `CreateMemory`, which shares `CreateFileCore`
with `CreateFile`): each pair's `Tool` subclass holds one of `ReadFileCore`/`EditFileCore`/
`CreateFileCore` as a member (`self.read_file_core`/`self.edit_file_core`/
`self.create_file_core`) and delegates nearly all of its `apply()` logic to it, so each mechanic
is written and tested once. What differs between a file tool and its scratchpad/memory
counterpart — resolving the target `Path` (a model-supplied `filename` checked against
`readDirs`/`writeDirs`, vs. the fixed, harness-managed `Session.scratchpad.path` with no
permission check at all, vs. a namespace-resolved memory path — see
`klorb.tools.memory.common`), and a couple of description strings — stays in each `Tool`
subclass; everything else (argument validation, the line-range read/drift-tolerant substitution/
file-creation algorithm, and building the result dict) lives in this package: `read_file_core.py`
(`ReadFileCore`), `edit_file_core.py` (`EditFileCore`, `LineRangeEdit`), and
`create_file_core.py` (`CreateFileCore`), re-exported here so callers use `from klorb.tools.util
import ReadFileCore, EditFileCore, CreateFileCore` regardless of which submodule actually
defines them.

Unlike `klorb.tools.scratchpad`, this package's `__init__.py` re-exporting `ReadFileCore`/
`EditFileCore`/`CreateFileCore` creates no import cycle: none of them takes a `ToolSetupContext`
or otherwise imports `klorb.tools.setup_context`/`klorb.session` — each is constructed from
plain arguments (an `int`, or nothing at all) and operates on a `pathlib.Path` its caller
already resolved. `walk_readable_tree` (`dir_walk.py`) is different — it does take a
`ToolSetupContext`, since walking a directory tree has to re-check `readDirs` at every
subdirectory — but re-exporting it here is still safe: nothing that needs to avoid the
`klorb.session` cycle (e.g. `klorb.session` itself, or `klorb.tools.ask.common`) imports
`klorb.tools.util`, only tool implementations (`GrepTool`, `FindFileTool`, ...) do.
"""

from klorb.tools.util.create_file_core import CreateFileCore
from klorb.tools.util.dir_walk import walk_readable_tree
from klorb.tools.util.edit_file_core import EditFileCore, LineRangeEdit
from klorb.tools.util.read_file_core import ReadFileCore

__all__ = ["CreateFileCore", "EditFileCore", "LineRangeEdit", "ReadFileCore", "walk_readable_tree"]
