# © Copyright 2026 Aaron Kimball
"""The named-list-concatenate merge `hooks`/`events` use across the config-layer stack, and the
parsers turning a raw `{name: [handler, ...]}` object into typed handler lists.
"""

import logging
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from klorb.hooks.config import (
    EVENT_CONFIG_MODELS,
    HOOK_NAMES,
    PROCESS_SCOPED_HOOK_NAMES,
    EventConfig,
    HookConfig,
    TimerEventConfig,
)
from klorb.hooks.timer_events import clamp_timer_intervals

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def concatenate_named_handler_lists(
    accumulator: dict[str, list[T]], layer_value: dict[str, list[T]],
) -> None:
    """Append `layer_value`'s per-name handler lists onto `accumulator`, in place. A name
    absent from `accumulator` starts with an empty list, so the final linear order for any one
    name is simply every layer's entries for that name, in the order `merge` is called across
    layers, then in the order each layer authored its own list.
    """
    for name, handlers in layer_value.items():
        accumulator.setdefault(name, []).extend(handlers)


def parse_handler_list(
    raw_handlers: Any, *, model: type[T], source_label: str,
) -> tuple[list[T], list[str]]:
    """Parse a raw JSON value (expected to be a list of handler-config dicts) into `model`
    instances, returning `(parsed, warnings)`. A `raw_handlers` that isn't a list contributes
    nothing; an individual entry that fails to validate against `model` is skipped rather than
    dropping the rest of the list.
    """
    warnings: list[str] = []
    if not isinstance(raw_handlers, list):
        warnings.append(f"{source_label}: expected a list of handler configs; ignoring.")
        return [], warnings
    parsed: list[T] = []
    for raw_handler in raw_handlers:
        try:
            parsed.append(model.model_validate(raw_handler))
        except ValidationError as exc:
            warnings.append(f"{source_label}: invalid handler config {raw_handler!r}: {exc}; ignoring entry.")
    return parsed, warnings


def parse_session_scoped_hook_dict(
    raw_hooks: Any, *, source_label: str, default_is_heritable: bool = False,
) -> dict[str, list[HookConfig]]:
    """Parse a raw `{hookName: [handler, ...]}` object into `HookConfig` lists, keyed by hook
    name. A name outside `HOOK_NAMES`, or a `PROCESS_SCOPED_HOOK_NAMES` entry (a session-scoped
    grant may never add a process-wide handler), is dropped, logged as a `logger.warning()`.
    Every parsed `HookConfig` whose raw dict didn't set `isHeritable` explicitly gets
    `is_heritable=default_is_heritable` forced onto it.
    """
    if not isinstance(raw_hooks, dict):
        logger.warning("%s must be an object; got %r", source_label, raw_hooks)
        return {}
    result: dict[str, list[HookConfig]] = {}
    for name, raw_handlers in raw_hooks.items():
        if name not in HOOK_NAMES:
            logger.warning("%s names unrecognized hook %r; ignoring.", source_label, name)
            continue
        if name in PROCESS_SCOPED_HOOK_NAMES:
            logger.warning(
                "%s names process-scoped hook %r, which may only be configured via "
                "klorb-config.json's top-level hooks key; ignoring.", source_label, name)
            continue
        parsed, warnings = parse_handler_list(
            raw_handlers, model=HookConfig, source_label=f"{source_label} ({name})")
        for warning in warnings:
            logger.warning(warning)
        if parsed:
            result[name] = [
                handler if "is_heritable" in handler.model_fields_set
                else handler.model_copy(update={"is_heritable": default_is_heritable})
                for handler in parsed
            ]
    return result


def parse_event_dict(raw_events: Any, *, source_label: str) -> dict[str, list[EventConfig]]:
    """Parse a raw `{eventName: [entry, ...]}` object into the right `EventConfig` subclass per
    `EVENT_CONFIG_MODELS`, keyed by event name. No event name is ever process-scoped, so nothing
    is rejected on those grounds.
    """
    if not isinstance(raw_events, dict):
        logger.warning("%s must be an object; got %r", source_label, raw_events)
        return {}
    result: dict[str, list[EventConfig]] = {}
    for name, raw_handlers in raw_events.items():
        model = EVENT_CONFIG_MODELS.get(name)
        if model is None:
            logger.warning("%s names unrecognized event %r; ignoring.", source_label, name)
            continue
        parsed, warnings = parse_handler_list(
            raw_handlers, model=model, source_label=f"{source_label} ({name})")
        for warning in warnings:
            logger.warning(warning)
        if name == "Timer":
            clamp_timer_intervals(
                cast("list[TimerEventConfig]", parsed), source_label=f"{source_label} ({name})",
                warnings=[])
        if parsed:
            result[name] = parsed
    return result
