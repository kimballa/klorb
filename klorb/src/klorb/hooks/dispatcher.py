# © Copyright 2026 Aaron Kimball
"""`HookDispatcher`: resolves the ordered handler chain a `ProcessConfig` carries for one hook
name, runs each eligible handler, and folds the results into a single aggregate `HookOutput`.
"""

import logging
from typing import TYPE_CHECKING, Any

from klorb.hooks.bash_handler import run_bash_handler
from klorb.hooks.config import HookConfig
from klorb.hooks.filters import evaluate_filter
from klorb.hooks.wire import HookInput, HookOutput
from klorb.session.config import SessionConfig

if TYPE_CHECKING:
    from klorb.process_config import ProcessConfig

logger = logging.getLogger(__name__)


def _handler_args(handler: HookConfig) -> dict[str, Any]:
    """The `HookInput.args` payload for `handler`: its own `shell`, `command`, or `prompt`,
    whichever it declares -- `{}` if it declares none (a malformed config Phase 1's config-load
    validation should already have warned about)."""
    if handler.shell is not None:
        return {"shell": handler.shell}
    if handler.command is not None:
        return {"command": handler.command}
    if handler.prompt is not None:
        return {"prompt": handler.prompt}
    return {}


def _fold(accumulated: HookOutput, latest: HookOutput) -> HookOutput:
    """Layer `latest` (one handler's result) onto `accumulated` (the chain so far): a field
    `latest` explicitly set wins, one it left at its default carries `accumulated`'s value
    forward. `success` is the strictest outcome seen so far (`False` once any valid handler
    says `False`, matching this plan's stricter-outcome-wins rule); `interrupt` is `True` once
    any valid handler asks for it."""
    return HookOutput(
        success=accumulated.success and latest.success,
        tool_args=latest.tool_args if latest.tool_args is not None else accumulated.tool_args,
        permission=latest.permission if latest.permission is not None else accumulated.permission,
        message=latest.message if latest.message is not None else accumulated.message,
        interrupt=latest.interrupt or accumulated.interrupt)


class HookDispatcher:
    """Dispatches one hook firing against a `ProcessConfig`'s configured handler chain for that
    hook name. `bash` handlers actually run; `classifier`/`chat` are recognized by the config
    schema but not yet dispatchable -- a handler of either type simply contributes nothing to
    the chain."""

    def __init__(self, process_config: "ProcessConfig") -> None:
        self._process_config = process_config

    def dispatch(
        self, hook_name: str, hook_input: HookInput, *,
        session_config: SessionConfig | None = None,
    ) -> HookOutput:
        """Run every eligible handler configured for `hook_name`, in the order
        `klorb.process_config.load_process_config` already resolved, folding each valid
        `HookOutput` into the next handler's `HookInput` (its `message`/`tool_args`) and into
        the aggregate result returned here. A handler is skipped -- contributing nothing --
        when its `filter` doesn't match `hook_input.event`, its `type` isn't yet dispatchable,
        or it fails per `run_bash_handler`'s "Error handling" contract; this method itself
        never raises. `session_config` sandboxes a `bash` handler with a live session's
        permission tables when one exists (`onSessionStart`/`onSessionEnd`); otherwise falls
        back to `ProcessConfig.session`, the template every fresh session is copied from (there
        is no live session yet for `onProcessStart`/`onProcessEnd`).
        """
        handlers = self._process_config.hooks.get(hook_name, [])
        logger.debug("Dispatching hook %r (%d configured handler(s))", hook_name, len(handlers))
        if not handlers:
            return HookOutput()
        sandbox_config = session_config if session_config is not None else self._process_config.session
        subject = hook_input.event or ""
        aggregate = HookOutput()
        chained_input = hook_input
        for handler in handlers:
            if handler.filter is not None and not evaluate_filter(handler.filter, subject):
                continue
            result = self._run_handler(handler, chained_input, sandbox_config)
            if result is None:
                continue
            aggregate = _fold(aggregate, result)
            chained_input = chained_input.model_copy(update={
                "message": result.message if result.message is not None else chained_input.message,
                "tool_args": result.tool_args if result.tool_args is not None else chained_input.tool_args,
            })
        return aggregate

    def _run_handler(
        self, handler: HookConfig, hook_input: HookInput, session_config: SessionConfig,
    ) -> HookOutput | None:
        handler_input = hook_input.model_copy(
            update={"name": handler.name, "args": _handler_args(handler)})
        if handler.type != "bash":
            logger.debug(
                "Hook handler type %r for %r not yet dispatchable; skipping.",
                handler.type, hook_input.hook)
            return None
        timeout = (
            self._process_config.hook_bash_timeout_seconds
            if self._process_config.hook_bash_timeout_seconds is not None
            else self._process_config.bash_timeout_seconds)
        return run_bash_handler(
            handler, handler_input, session_config=session_config,
            bash_command=self._process_config.bash_command, timeout_seconds=timeout)
