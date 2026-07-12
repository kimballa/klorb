# © Copyright 2026 Aaron Kimball
"""Command palette provider and modal that report everything klorb knows about the active
model: its family/version, capabilities (vision, thinking, context window, etc.), and
published per-token pricing, when available."""

from typing import Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from klorb.models.model import Model

SHOW_MODEL_INFO_LABEL = "Show model info"
MODEL_INFO_HEADER_TEXT = "Model info:"
NOT_AVAILABLE = "(not available)"

_CAPABILITY_LABELS: dict[str, str] = {
    "vision": "Vision",
    "thinking": "Thinking",
    "max_context_window": "Max context window",
    "max_output_tokens": "Max output tokens",
    "function_calling": "Function calling",
    "streaming": "Streaming",
}
"""Display label for each standard `Model.capabilities()` key (see
docs/specs/model-framework.md), in the order they're shown. `thinking_budget_style` is
folded into the `Thinking` row rather than shown as its own row (see
`_format_capabilities`); any other, provider-specific key is shown afterwards under its raw
dict key, verbatim."""


class SupportsModelInfo(Protocol):
    """Structural interface for an App that can report its currently active `Model`."""

    def get_active_model(self) -> Model | None:
        """Return the currently active `Model`, or `None` if `config.model` isn't a
        registered model."""

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Report a one-off status/result in the history scroll."""


def _format_capability_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key in ("max_context_window", "max_output_tokens") and isinstance(value, int):
        return f"{value:,} tokens"
    return str(value)


def _format_capabilities(capabilities: dict[str, object]) -> list[str]:
    """Render `capabilities` as `"Label: value"` lines, in `_CAPABILITY_LABELS`' order first
    (skipping any that are absent), then any remaining provider-specific keys verbatim, sorted
    for a stable order. A `True` `"thinking"` value gets its `"thinking_budget_style"`
    (default `"effort"`, see `klorb.session.Session._reasoning_params`) appended in
    parentheses rather than shown as its own row, since the budget style is meaningless
    without thinking also being enabled.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for key, label in _CAPABILITY_LABELS.items():
        if key not in capabilities:
            continue
        seen.add(key)
        value = _format_capability_value(key, capabilities[key])
        if key == "thinking" and capabilities[key]:
            budget_style = capabilities.get("thinking_budget_style", "effort")
            value = f"{value} ({budget_style})"
        lines.append(f"{label}: {value}")
    seen.add("thinking_budget_style")
    for key in sorted(set(capabilities) - seen):
        lines.append(f"{key}: {_format_capability_value(key, capabilities[key])}")
    return lines


def format_model_info(model: Model) -> str:
    """Render every field klorb tracks for `model` as human-readable `"Label: value"` lines,
    joined with newlines — the body `ModelInfoScreen` displays, and independently testable
    without constructing a Textual app.
    """
    lines = [
        f"Name: {model.name()}",
        f"Family: {model.family() or NOT_AVAILABLE}",
        f"Version: {model.model_version() or NOT_AVAILABLE}",
    ]
    lines.extend(_format_capabilities(model.capabilities()))
    pricing = model.pricing()
    if pricing is None:
        lines.append(f"Cost per MTok (in / out): {NOT_AVAILABLE}")
    else:
        lines.append(
            "Cost per MTok (in / out): "
            f"{pricing.input_cost_per_mtok:g} / {pricing.output_cost_per_mtok:g} {pricing.currency}")
    return "\n".join(lines)


class ModelInfoScreen(ModalScreen[None]):
    """Modal showing `format_model_info(model)`'s rendering of the active model's tracked
    data. Escape or Enter dismisses; there's nothing to select.
    """

    CSS = """
    ModelInfoScreen {
        align: center middle;
    }

    ModelInfoScreen Vertical {
        width: auto;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 0 1;
    }

    #model-info-header {
        text-style: bold;
        margin: 0 0 1 0;
        width: auto;
    }

    #model-info-body {
        width: auto;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("enter", "dismiss", "Close")]

    def __init__(self, model: Model) -> None:
        super().__init__()
        self._model = model

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(MODEL_INFO_HEADER_TEXT, id="model-info-header"),
            Static(format_model_info(self._model), markup=False, id="model-info-body"),
        )


class ModelInfoCommandProvider(Provider):
    """Offers a `"Show model info"` command via the command palette that opens
    `ModelInfoScreen` for the currently active model, or a plain notice if no model is
    currently registered/active.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(SHOW_MODEL_INFO_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(SHOW_MODEL_INFO_LABEL), self._show_model_info_screen)

    async def discover(self) -> Hits:
        yield DiscoveryHit(SHOW_MODEL_INFO_LABEL, self._show_model_info_screen)

    def _show_model_info_screen(self) -> None:
        app = cast(SupportsModelInfo, self.app)
        model = app.get_active_model()
        if model is None:
            app.show_notice("The active model isn't registered; no info to show.", error=True)
            return
        self.app.push_screen(ModelInfoScreen(model))
