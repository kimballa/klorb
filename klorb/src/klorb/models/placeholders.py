# © Copyright 2026 Aaron Kimball
"""Virtual `klorb-default/*` model names an `agents.json` entry or `CreateSubagent` caller may
use in place of a literal model id. See docs/specs/subagents.md."""

PLACEHOLDER_MODEL_PREFIX = "klorb-default/"
PLACEHOLDER_MODEL_FAST = "klorb-default/fast"
PLACEHOLDER_MODEL_NORMAL = "klorb-default/normal"
PLACEHOLDER_MODEL_HEAVY = "klorb-default/heavy"
PLACEHOLDER_MODEL_CURRENT = "klorb-default/current"

DEFAULT_PLACEHOLDER_MODEL_FAST = "xiaomi/mimo-v2.5"
DEFAULT_PLACEHOLDER_MODEL_NORMAL = "xiaomi/mimo-v2.5-pro"
DEFAULT_PLACEHOLDER_MODEL_HEAVY = "z-ai/glm-5.2"

_PLACEHOLDER_SUFFIXES = {
    "fast": PLACEHOLDER_MODEL_FAST,
    "normal": PLACEHOLDER_MODEL_NORMAL,
    "heavy": PLACEHOLDER_MODEL_HEAVY,
    "current": PLACEHOLDER_MODEL_CURRENT,
}


def resolve_placeholder_model(model: str, *, fast: str, normal: str, heavy: str, current: str) -> str:
    """Resolves a `klorb-default/*` placeholder to a concrete model id; any other string passes
    through unchanged. Raises `ValueError` for an unrecognized `klorb-default/*` suffix."""
    if not model.startswith(PLACEHOLDER_MODEL_PREFIX):
        return model
    suffix = model[len(PLACEHOLDER_MODEL_PREFIX):]
    if suffix not in _PLACEHOLDER_SUFFIXES:
        raise ValueError(
            f"Unrecognized placeholder model {model!r}; expected one of "
            f"{sorted(_PLACEHOLDER_SUFFIXES.values())}.")
    return {"fast": fast, "normal": normal, "heavy": heavy, "current": current}[suffix]
