# © Copyright 2026 Aaron Kimball
"""`ReplAppBase`: attribute-only declarations shared by every `ReplApp` mixin.

`ReplApp` itself (`klorb.tui.app`) is composed from several mixins, each holding one
cohesive slice of its ~150 methods, verbatim -- `self.foo(...)` resolves at runtime via MRO
regardless of which mixin `foo` physically lives in, so no call site needs to change. But
this repo's `make typecheck` runs mypy with `--disallow-untyped-calls` and
`--disallow-untyped-globals`, so a mixin method referencing `self._some_attr` (set by
`ReplApp.__init__`, which lives in a different file) needs `_some_attr` visible on `self`'s
declared type from that mixin's own point of view -- mypy doesn't know that whichever
concrete class eventually mixes this one in will also mix in the one that sets it.
`ReplAppBase` is that shared point of view: every mixin declares
`class FooMixin(ReplAppBase):`, and `ReplApp(FooMixin, ..., ReplAppBase)` is the only class
with a real `__init__` body. This class carries no behavior of its own.
"""

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from textual.app import App

from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.tui.widgets.tool_call_widgets import RunningToolCallStatic, ToolCallStatic
from klorb.watchdog import LivenessWatchdog
from klorb.workspace import TrustManager


class ReplAppBase(App[None]):
    """Attribute declarations for every field `ReplApp.__init__` sets on `self`, so each
    mixin file type-checks on its own despite referencing state that a different mixin (or
    `ReplApp` itself) sets up. See the module docstring for why this class exists.
    """

    _process_config: ProcessConfig
    _session: Session
    _initial_message: str | None
    _session_log_enabled: bool
    _trust_manager: TrustManager | None
    _config_flag_path: Path | None
    _cancel_event: threading.Event | None
    _shell_cancel_event: threading.Event | None
    _last_ctrl_c_at: float
    _last_ctrl_c_kind: Literal["copy", "interrupt", "bare"] | None
    _interrupt_notice_shown: bool
    _watchdog: LivenessWatchdog
    _turn_in_flight: bool
    _interaction_lock: asyncio.Lock
    _release_pending_interaction: Callable[[], None] | None
    _exit_requested: bool
    _last_permission_action: Literal["allow", "deny"]
    _last_permission_scope: Literal["once", "session", "workspace", "homedir"]
    _tool_call_widgets: list[ToolCallStatic]
    _running_tool_call_widgets: dict[str, RunningToolCallStatic]
    _tool_call_detail_shown: bool
    _history_pinned_to_bottom: bool
