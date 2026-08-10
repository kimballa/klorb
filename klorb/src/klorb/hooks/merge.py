# © Copyright 2026 Aaron Kimball
"""The named-list-concatenate merge `hooks`/`events` use across the config-layer stack: each
layer contributes a dict keyed by hook/event name, and layers are combined by appending a later
layer's list for a name onto whatever earlier layers already built for that same name.
"""

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

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
    dropping the rest of the list — either way, a human-readable message naming
    `source_label` is added to `warnings` instead of raising.
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
