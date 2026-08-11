# © Copyright 2026 Aaron Kimball
"""`HookDispatcher`: resolves the ordered handler chain a `ProcessConfig` carries for one hook
name, runs each eligible handler, and folds the results into a single aggregate `HookOutput`.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from klorb.api_provider import ApiProvider
from klorb.hooks.bash_handler import run_bash_handler
from klorb.hooks.classifier_handler import run_classifier_handler
from klorb.hooks.config import HOOK_FILTER_SUBJECT_FIELDS, EventConfig, HookConfig
from klorb.hooks.filters import evaluate_filter
from klorb.hooks.hook_api import EventInput, HookInput, HookOutput
from klorb.models.registry import ModelRegistry
from klorb.permissions.table import Verdict, stricter_verdict
from klorb.session.config import SessionConfig
from klorb.session_naming import NANO_CLASSIFIER_CAPABILITY

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


def _fold_permission(accumulated: Verdict | None, latest: Verdict | None) -> Verdict | None:
    """Reduce two handlers' `permission` verdicts the same allow/ask/deny way
    `klorb.permissions.table.stricter_verdict` reduces any other verdict pair: a handler that
    left `permission` unset (`None`) contributes no opinion and never moves the aggregate toward
    `deny`; once two handlers *have* opined, the stricter of the two wins."""
    if latest is None:
        return accumulated
    if accumulated is None:
        return latest
    return stricter_verdict(accumulated, latest)


def _fold(accumulated: HookOutput, latest: HookOutput) -> HookOutput:
    """Layer `latest` (one handler's result) onto `accumulated` (the chain so far): a field
    `latest` explicitly set wins, one it left at its default carries `accumulated`'s value
    forward -- `log` folds the same way as `message`. `success` is the strictest outcome seen so
    far (`False` once any valid handler says `False`); `permission` is the strictest allow/ask/
    deny verdict any handler that opined expressed (see `_fold_permission`); `interrupt`/
    `reset_session` are `True` once any valid handler asks for them."""
    return HookOutput(
        success=accumulated.success and latest.success,
        tool_args=latest.tool_args if latest.tool_args is not None else accumulated.tool_args,
        permission=_fold_permission(accumulated.permission, latest.permission),
        message=latest.message if latest.message is not None else accumulated.message,
        tool_result=latest.tool_result if latest.tool_result is not None else accumulated.tool_result,
        interrupt=latest.interrupt or accumulated.interrupt,
        reset_session=latest.reset_session or accumulated.reset_session,
        log=latest.log if latest.log is not None else accumulated.log)


class HookDispatcher:
    """Dispatches one hook firing against a `ProcessConfig`'s configured handler chain for that
    hook name: `bash` runs a sandboxed subprocess, `classifier` appeals to a small model for a
    structured judgment, `chat` contributes its own configured `prompt` text as `message`
    verbatim -- the caller (a `Session` turn/tool hook point) decides what to do with an
    aggregate `message`, e.g. starting a follow-up turn."""

    def __init__(
        self, process_config: "ProcessConfig", *,
        api_provider: ApiProvider | None = None, model_registry: ModelRegistry | None = None,
    ) -> None:
        self._process_config = process_config
        self._api_provider = api_provider
        """Used only by a `type="classifier"` handler's request. `None` when no live session
        exists yet to supply one (`onProcessStart`, before `klorb.cli.main()` constructs its
        `ApiProvider`) -- a classifier handler configured for such a hook simply contributes
        nothing, logged at `warning`, per hooks' general error-handling contract."""
        self._model_registry = model_registry
        """Used only by a `type="classifier"` handler to resolve a default model (`ProcessConfig.
        session_classifier_model` unset) via the same `NANO_CLASSIFIER_CAPABILITY` lookup
        `klorb.session_naming.default_naming_model` uses. `None` falls back to
        `DEFAULT_SESSION_CLASSIFIER_MODEL` directly, skipping the capability lookup."""

    def dispatch(
        self, hook_name: str, hook_input: HookInput, *,
        session_config: SessionConfig | None = None,
    ) -> HookOutput:
        """Run every eligible handler configured for `hook_name`, in the order
        `klorb.process_config.load_process_config` already resolved, folding each valid
        `HookOutput` into the next handler's `HookInput` (its `message`/`tool_args`) and into
        the aggregate result returned here. A handler is skipped -- contributing nothing --
        when its `filter` doesn't match the subject `klorb.hooks.config.
        HOOK_FILTER_SUBJECT_FIELDS` maps `hook_name` to (`hook_input.reason`/`message`/
        `tool_name`), or it fails per its own handler function's "Error handling" contract;
        this method itself never raises. `session_config` sandboxes a `bash` handler with a
        live session's permission tables when one exists; otherwise falls back to
        `ProcessConfig.session`, the template every fresh session is copied from (there is no
        live session yet for `onProcessStart`/`onProcessEnd`).
        """
        handlers = self._process_config.hooks.get(hook_name, [])
        logger.debug("Dispatching hook %r (%d configured handler(s))", hook_name, len(handlers))
        if not handlers:
            return HookOutput()
        sandbox_config = session_config if session_config is not None else self._process_config.session
        return self._run_chain(hook_name, handlers, hook_input, sandbox_config)

    def dispatch_event(
        self, event_name: str, entries: Sequence[EventConfig], event_input: EventInput, *,
        session_config: SessionConfig | None = None,
    ) -> HookOutput:
        """Run each of `entries`' own `action` as one ordered chain, under the same
        filter/fold/error-handling contract `dispatch()` applies to a hook's handler list.
        `entries` is whatever subset of `ProcessConfig.events[event_name]` the caller has
        already decided is eligible for this occurrence (e.g. a `FileSystemModified` watcher's
        own watch-path match)."""
        logger.debug("Dispatching event %r (%d configured handler(s))", event_name, len(entries))
        if not entries:
            return HookOutput()
        sandbox_config = session_config if session_config is not None else self._process_config.session
        handlers = list(map(lambda entry: entry.action, entries))
        return self._run_chain(event_name, handlers, event_input, sandbox_config)

    def _run_chain(
        self, name: str, handlers: list[HookConfig], hook_input: HookInput,
        session_config: SessionConfig,
    ) -> HookOutput:
        """Run `handlers` in order against `hook_input`, folding each valid `HookOutput` into
        the next handler's input and into the aggregate result."""
        subject_field = HOOK_FILTER_SUBJECT_FIELDS.get(name, "reason")
        subject = getattr(hook_input, subject_field, None) or ""
        aggregate = HookOutput()
        chained_input = hook_input
        for handler in handlers:
            if handler.filter is not None and not evaluate_filter(handler.filter, subject):
                continue
            result = self._run_handler(handler, chained_input, session_config)
            if result is None:
                continue
            if result.log is not None:
                logger.info("Hook %r handler %r: %s", name, handler.name, result.log)
            aggregate = _fold(aggregate, result)
            chained_input = chained_input.model_copy(update={
                "message": result.message if result.message is not None else chained_input.message,
                "tool_args": result.tool_args if result.tool_args is not None else chained_input.tool_args,
                "tool_result": (
                    result.tool_result if result.tool_result is not None else chained_input.tool_result),
            })
        if aggregate.reset_session and not aggregate.message:
            logger.warning(
                "Hook %r's aggregate result set reset_session without a message; ignoring "
                "reset_session.", name)
            aggregate = aggregate.model_copy(update={"reset_session": False})
        return aggregate

    def _run_handler(
        self, handler: HookConfig, hook_input: HookInput, session_config: SessionConfig,
    ) -> HookOutput | None:
        handler_input = hook_input.model_copy(
            update={"name": handler.name, "args": _handler_args(handler)})
        if handler.type == "bash":
            timeout = (
                self._process_config.hook_bash_timeout_seconds
                if self._process_config.hook_bash_timeout_seconds is not None
                else self._process_config.bash_timeout_seconds)
            return run_bash_handler(
                handler, handler_input, session_config=session_config,
                bash_command=self._process_config.bash_command, timeout_seconds=timeout)
        if handler.type == "classifier":
            return self._run_classifier_handler(handler, handler_input)
        assert handler.type == "chat"
        if handler.prompt is None:
            logger.warning(
                "Hook handler %r for %r has type=chat but no prompt set; skipping.",
                handler.name, hook_input.hook)
            return None
        return HookOutput(message=handler.prompt)

    def _run_classifier_handler(
        self, handler: HookConfig, hook_input: HookInput,
    ) -> HookOutput | None:
        if handler.prompt is None:
            logger.warning(
                "Hook handler %r for %r has type=classifier but no prompt set; skipping.",
                handler.name, hook_input.hook)
            return None
        if self._api_provider is None:
            logger.warning(
                "Hook handler %r for %r has type=classifier but no ApiProvider is available "
                "at this lifecycle moment; skipping.", handler.name, hook_input.hook)
            return None
        return run_classifier_handler(
            handler.prompt, hook_input, api_provider=self._api_provider,
            model=self._resolve_classifier_model(),
            timeout=self._process_config.session_classifier_timeout_seconds,
            e2e_timeout=self._process_config.session_classifier_e2e_timeout_seconds)

    def _resolve_classifier_model(self) -> str:
        """The model a `type="classifier"` handler's request is sent to: `ProcessConfig.
        session_classifier_model` if set, else a registered model volunteering
        `NANO_CLASSIFIER_CAPABILITY` (if `self._model_registry` was given one), else
        `DEFAULT_SESSION_CLASSIFIER_MODEL`."""
        # Deferred: `klorb.process_config` imports from `klorb.hooks.config`/`klorb.hooks.merge`,
        # so a module-level import here would be circular.
        from klorb.process_config import DEFAULT_SESSION_CLASSIFIER_MODEL

        if self._process_config.session_classifier_model is not None:
            return self._process_config.session_classifier_model
        if self._model_registry is not None:
            model = self._model_registry.find_by_capability(NANO_CLASSIFIER_CAPABILITY)
            if model is not None:
                return model.name()
        return DEFAULT_SESSION_CLASSIFIER_MODEL
