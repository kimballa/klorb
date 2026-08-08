# © Copyright 2026 Aaron Kimball
from klorb.hooks.config import HookConfig
from klorb.hooks.merge import concatenate_named_handler_lists, parse_handler_list


def test_concatenate_appends_a_new_layers_list_onto_an_existing_name() -> None:
    accumulator: dict[str, list[HookConfig]] = {
        "onProcessStart": [HookConfig(type="bash", shell="echo one")],
    }
    concatenate_named_handler_lists(
        accumulator, {"onProcessStart": [HookConfig(type="bash", shell="echo two")]})

    assert [handler.shell for handler in accumulator["onProcessStart"]] == ["echo one", "echo two"]


def test_concatenate_starts_a_new_name_with_an_empty_list() -> None:
    accumulator: dict[str, list[HookConfig]] = {}
    concatenate_named_handler_lists(
        accumulator, {"onSessionStart": [HookConfig(type="bash", shell="echo hi")]})

    assert list(accumulator.keys()) == ["onSessionStart"]
    assert len(accumulator["onSessionStart"]) == 1


def test_parse_handler_list_validates_each_entry() -> None:
    parsed, warnings = parse_handler_list(
        [{"type": "bash", "shell": "echo hi"}], model=HookConfig, source_label="test-layer")

    assert warnings == []
    assert len(parsed) == 1
    assert parsed[0].type == "bash"
    assert parsed[0].shell == "echo hi"


def test_parse_handler_list_skips_an_invalid_entry_and_keeps_the_rest() -> None:
    parsed, warnings = parse_handler_list(
        [{"type": "bash", "shell": "echo hi"}, {"type": "not-a-real-type"}],
        model=HookConfig, source_label="test-layer")

    assert len(parsed) == 1
    assert len(warnings) == 1
    assert "test-layer" in warnings[0]


def test_parse_handler_list_warns_when_value_is_not_a_list() -> None:
    parsed, warnings = parse_handler_list(
        {"not": "a list"}, model=HookConfig, source_label="test-layer")

    assert parsed == []
    assert len(warnings) == 1
    assert "test-layer" in warnings[0]
