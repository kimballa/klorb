# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.models."""

import json
from unittest.mock import MagicMock, patch

import pytest

from klorb.cli.models import run_models_cli
from klorb.models.model import Model
from klorb.models.openrouter_pricing import ModelPricing


def _make_model(
    name: str,
    *,
    family: str | None = "fam",
    model_version: str | None = "1.0",
    capabilities: dict | None = None,
    settings: dict | None = None,
    klorb_capabilities: dict | None = None,
) -> MagicMock:
    model = MagicMock(spec=Model)
    model.name.return_value = name
    model.family.return_value = family
    model.model_version.return_value = model_version
    model.capabilities.return_value = capabilities if capabilities is not None else {
        "vision": True, "thinking": False, "max_context_window": 1_000, "max_output_tokens": 500,
        "function_calling": True, "streaming": True,
    }
    model.settings.return_value = settings if settings is not None else {}
    model.klorb_capabilities.return_value = klorb_capabilities if klorb_capabilities is not None else {}
    return model


def test_run_models_cli_prints_table_sorted_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    model_b = _make_model("b/model-two")
    model_a = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        exit_code = run_models_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split()[0] == "NAME"
    assert set(lines[1]) == {"-"}
    assert out.index("a/model-one") < out.index("b/model-two")


def test_run_models_cli_table_has_no_vertical_borders_and_one_rule(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        run_models_cli([])

    lines = capsys.readouterr().out.splitlines()
    assert "|" not in "\n".join(lines)
    rule_lines = [line for line in lines if set(line) == {"-"}]
    assert len(rule_lines) == 1
    assert rule_lines[0] == lines[1]


def test_run_models_cli_brief_prints_only_names(capsys: pytest.CaptureFixture[str]) -> None:
    model_b = _make_model("b/model-two")
    model_a = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        exit_code = run_models_cli(["--brief"])

    assert exit_code == 0
    assert capsys.readouterr().out == "a/model-one\nb/model-two\n"


def test_run_models_cli_json_and_brief_emit_array_of_name_strings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_b = _make_model("b/model-two")
    model_a = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        exit_code = run_models_cli(["--json", "--brief"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == ["a/model-one", "b/model-two"]


def test_run_models_cli_brief_never_fetches_costs(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch("klorb.cli.models.fetch_openrouter_pricing_for_models") as mock_fetch:
            run_models_cli(["--brief", "--costs"])

    mock_fetch.assert_not_called()


def test_run_models_cli_json_emits_array_of_model_dicts(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model(
        "a/model-one", family="fam", model_version="1.0",
        capabilities={"vision": True}, settings={"temperature": 0.1},
        klorb_capabilities={"FOO": True})
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        exit_code = run_models_cli(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{
        "name": "a/model-one",
        "family": "fam",
        "model_version": "1.0",
        "settings": {"temperature": 0.1},
        "capabilities": {"vision": True},
        "klorb_capabilities": {"FOO": True},
    }]


def test_run_models_cli_json_has_no_costs_key_without_flag(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        run_models_cli(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "costs" not in payload[0]


def test_run_models_cli_json_costs_includes_pricing(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    pricing = ModelPricing(input_cost_per_mtok=1.5, output_cost_per_mtok=3.0)
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch(
            "klorb.cli.models.fetch_openrouter_pricing_for_models", return_value={"a/model-one": pricing},
        ):
            exit_code = run_models_cli(["--json", "--costs"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["costs"] == {
        "input_cost_per_mtok": 1.5,
        "output_cost_per_mtok": 3.0,
        "currency": "USD",
    }


def test_run_models_cli_json_costs_null_when_pricing_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch(
            "klorb.cli.models.fetch_openrouter_pricing_for_models", return_value={"a/model-one": None},
        ):
            run_models_cli(["--json", "--costs"])

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["costs"] is None


def test_run_models_cli_costs_adds_table_columns(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    pricing = ModelPricing(input_cost_per_mtok=1.5, output_cost_per_mtok=3.0)
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch(
            "klorb.cli.models.fetch_openrouter_pricing_for_models", return_value={"a/model-one": pricing},
        ):
            run_models_cli(["--costs"])

    out = capsys.readouterr().out
    assert "IN $/MTOK" in out
    assert "OUT $/MTOK" in out
    assert "1.500" in out
    assert "3.000" in out


def test_run_models_cli_costs_passes_all_model_names(capsys: pytest.CaptureFixture[str]) -> None:
    model_a = _make_model("a/model-one")
    model_b = _make_model("b/model-two")
    with patch("klorb.cli.models.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        with patch("klorb.cli.models.fetch_openrouter_pricing_for_models", return_value={}) as mock_fetch:
            run_models_cli(["--costs"])

    mock_fetch.assert_called_once_with(["a/model-one", "b/model-two"])
