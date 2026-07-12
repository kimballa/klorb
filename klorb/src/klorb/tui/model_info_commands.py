# © Copyright 2026 Aaron Kimball
"""Command palette provider that reports everything klorb knows about the active model: its
family/version, capabilities (vision, thinking, context window, etc.), klorb-curated
capability flags, and current per-token pricing (fetched live from OpenRouter — see
`klorb.models.openrouter_pricing`)."""

import asyncio
from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

from klorb.models.model import Model
from klorb.models.openrouter_pricing import ModelPricing, fetch_openrouter_pricing

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


def _format_klorb_capabilities(klorb_capabilities: dict[str, object]) -> str:
    if not klorb_capabilities:
        return NOT_AVAILABLE
    return ", ".join(
        f"{key}={_format_capability_value(key, value)}"
        for key, value in sorted(klorb_capabilities.items()))


def format_model_info(model: Model, pricing: ModelPricing | None) -> str:
    """Render every field klorb tracks for `model` as human-readable `"Label: value"` lines,
    joined with newlines — the body of the history notice
    `ModelInfoCommandProvider` posts, and independently testable without constructing a
    Textual app or a network connection. `pricing` is looked up separately
    (`fetch_openrouter_pricing`, live, not stored on `model` — see
    docs/adrs/fetch-model-pricing-live-not-from-json.md) and passed in rather than read off
    `model`, since fetching it is a blocking network call this function itself must not make.
    """
    lines = [
        f"Name: {model.name()}",
        f"Family: {model.family() or NOT_AVAILABLE}",
        f"Version: {model.model_version() or NOT_AVAILABLE}",
    ]
    lines.extend(_format_capabilities(model.capabilities()))
    lines.append(f"Klorb capabilities: {_format_klorb_capabilities(model.klorb_capabilities())}")
    if pricing is None:
        lines.append(f"Cost per MTok (in / out): {NOT_AVAILABLE}")
    else:
        lines.append(
            "Cost per MTok (in / out): "
            f"{pricing.input_cost_per_mtok:g} / {pricing.output_cost_per_mtok:g} {pricing.currency}")
    return "\n".join(lines)


class ModelInfoCommandProvider(Provider):
    """Offers a `"Show model info"` command via the command palette that appends
    `format_model_info`'s rendering of the currently active model to the history scroll (see
    `SupportsModelInfo.show_notice`), or a plain notice if no model is currently
    registered/active.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(SHOW_MODEL_INFO_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(SHOW_MODEL_INFO_LABEL), self._show_model_info)

    async def discover(self) -> Hits:
        yield DiscoveryHit(SHOW_MODEL_INFO_LABEL, self._show_model_info)

    async def _show_model_info(self) -> None:
        app = cast(SupportsModelInfo, self.app)
        model = app.get_active_model()
        if model is None:
            app.show_notice("The active model isn't registered; no info to show.", error=True)
            return
        pricing = await asyncio.to_thread(fetch_openrouter_pricing, model.name())
        app.show_notice(f"{MODEL_INFO_HEADER_TEXT}\n{format_model_info(model, pricing)}")
