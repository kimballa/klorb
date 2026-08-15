# © Copyright 2026 Aaron Kimball
"""`klorb models` subcommand entry point."""

import argparse
import json
from typing import Any

from klorb.cli._common import MODELS_SUBCOMMAND, TABLE_GUTTER
from klorb.models.model import Model
from klorb.models.openrouter_pricing import (
    MAX_PRICING_REQUESTS_PER_SECOND,
    ModelPricing,
    fetch_openrouter_pricing_for_models,
)
from klorb.models.registry import ModelRegistry

_MODELS_TABLE_HEADERS = [
    "NAME", "FAMILY", "VERSION", "CONTEXT", "MAX OUTPUT", "VISION", "THINKING", "TOOLS", "STREAM",
]
"""Column headers for `klorb models`' default table output, in the order each model's row is
built by `_model_table_row`. `--costs` appends `IN $/MTOK`/`OUT $/MTOK` after these."""


def build_models_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb models`'s own flags
    (`--json`/`--brief`/`--costs`) — see `run_models_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog=f"klorb {MODELS_SUBCOMMAND}",
        description="List every model klorb has discovered (built-in and user-added).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help=(
            "Emit a JSON array of each model's data instead of a table. Combined with "
            "--brief, emits a JSON array of model name strings instead."
        ),
    )
    parser.add_argument(
        "--brief", action="store_true",
        help=(
            "Emit only each model's OpenRouter name, no other fields: one per line as plain "
            "text, or (combined with --json) as a JSON array of strings."
        ),
    )
    parser.add_argument(
        "--costs", action="store_true",
        help=(
            "Look up each model's current per-token cost from OpenRouter (live, throttled to "
            f"{MAX_PRICING_REQUESTS_PER_SECOND:g} requests/second — see "
            "klorb.models.openrouter_pricing.MAX_PRICING_REQUESTS_PER_SECOND) and include it "
            "in the output. Ignored with --brief, which never prints anything but names."
        ),
    )
    return parser


def _format_int(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "-"


def _format_bool(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def _model_table_row(model: Model, pricing: ModelPricing | None, *, include_costs: bool) -> list[str]:
    capabilities = model.capabilities()
    row = [
        model.name(),
        model.family() or "-",
        model.model_version() or "-",
        _format_int(capabilities.get("max_context_window")),
        _format_int(capabilities.get("max_output_tokens")),
        _format_bool(capabilities.get("vision")),
        _format_bool(capabilities.get("thinking")),
        _format_bool(capabilities.get("function_calling")),
        _format_bool(capabilities.get("streaming")),
    ]
    if include_costs:
        if pricing is None:
            row += ["-", "-"]
        else:
            row += [f"{pricing.input_cost_per_mtok:.3f}", f"{pricing.output_cost_per_mtok:.3f}"]
    return row


def _render_models_table(models: list[Model], costs: dict[str, ModelPricing | None] | None) -> str:
    """Render `models` as a column-aligned table with no vertical borders between columns and a
    single horizontal rule under the header row (and nowhere else). `costs` (from `--costs`),
    if given, appends an input/output $-per-MTok column pair; a model with no live pricing
    available shows `-` in both.
    """
    headers = list(_MODELS_TABLE_HEADERS)
    if costs is not None:
        headers += ["IN $/MTOK", "OUT $/MTOK"]

    rows: list[list[str]] = []
    for model in models:
        pricing = costs.get(model.name()) if costs is not None else None
        rows.append(_model_table_row(model, pricing, include_costs=costs is not None))

    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]

    right_justified = {"CONTEXT", "MAX OUTPUT", "IN $/MTOK", "OUT $/MTOK"}

    def render_row(values: list[str]) -> str:
        parts = []
        for value, header, width in zip(values, headers, widths):
            align = "rjust" if header in right_justified else "ljust"
            parts.append(getattr(value, align)(width))
        return TABLE_GUTTER.join(parts).rstrip()

    total_width = sum(widths) + len(TABLE_GUTTER) * (len(widths) - 1)
    lines = [render_row(headers), "-" * total_width]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def _model_to_dict(model: Model, pricing: ModelPricing | None, *, include_costs: bool) -> dict[str, Any]:
    """Return `model`'s data as a plain JSON-serializable dict, in the same shape as its source
    `klorb-model` JSON file's data (minus the `schema` envelope, which describes the file, not
    the model). When `include_costs` is set (`--json --costs`), adds a `costs` key: `None` if
    no live pricing could be found for this model, otherwise its per-MTok input/output cost —
    see `run_models_cli`.
    """
    data: dict[str, Any] = {
        "name": model.name(),
        "family": model.family(),
        "model_version": model.model_version(),
        "settings": model.settings(),
        "capabilities": model.capabilities(),
        "klorb_capabilities": model.klorb_capabilities(),
    }
    if include_costs:
        data["costs"] = None if pricing is None else {
            "input_cost_per_mtok": pricing.input_cost_per_mtok,
            "output_cost_per_mtok": pricing.output_cost_per_mtok,
            "currency": pricing.currency,
        }
    return data


def run_models_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb models`) and print every model
    `ModelRegistry` discovers (built-in and user-added, see docs/specs/model-framework.md) to
    stdout, sorted by name: a column-aligned table by default, a JSON array of each model's
    data with `--json`, or just each model's OpenRouter name and no other fields with
    `--brief` — one per line as plain text, or (combined with `--json`) as a JSON array of
    name strings.

    `--costs` looks up each model's live per-token pricing from OpenRouter
    (`klorb.models.openrouter_pricing.fetch_openrouter_pricing_for_models`, throttled to
    `MAX_PRICING_REQUESTS_PER_SECOND` requests/second) and folds it into whichever output
    format was chosen — an extra column pair in the table, or a `"costs"` key in each `--json`
    object. It's a no-op with `--brief`, which never fetches pricing since it never prints
    anything but names. Always returns `0`.
    """
    parser = build_models_parser()
    args = parser.parse_args(argv)

    models = sorted(ModelRegistry().models(), key=lambda model: model.name())

    if args.brief:
        names = [model.name() for model in models]
        if args.json:
            print(json.dumps(names, indent=2, ensure_ascii=False))
        else:
            for name in names:
                print(name)
        return 0

    costs: dict[str, ModelPricing | None] | None = None
    if args.costs:
        costs = fetch_openrouter_pricing_for_models([model.name() for model in models])

    if args.json:
        model_dicts = []
        for model in models:
            pricing = costs.get(model.name()) if costs is not None else None
            model_dicts.append(_model_to_dict(model, pricing, include_costs=args.costs))
        print(json.dumps(model_dicts, indent=2, ensure_ascii=False))
        return 0

    print(_render_models_table(models, costs))
    return 0
