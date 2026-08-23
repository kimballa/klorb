# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.registry."""
from collections.abc import Callable

import fixtures.sample_tools as sample_tools_package
import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools import registry as registry_module
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.registry import ToolRegistry

_TASKS_TOOL_NAMES = {"TodoList", "TodoNext", "TodoCreate", "TodoUpdate"}


def test_every_production_tool_overrides_category_and_is_read_only() -> None:
    """Every concrete `Tool` the default package discovers must override `category()` and
    `is_read_only()` (the base class raises `NotImplementedError`) -- the foundation for
    filtering a subagent's tool set by category and read-only-ness."""
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    failures: list[str] = []
    for tool in registry.tools():
        for method_name in ("category", "is_read_only"):
            try:
                getattr(tool, method_name)()
            except NotImplementedError:
                failures.append(f"{tool.name()}.{method_name}()")

    assert not failures, f"tools missing overrides: {failures}"


def _registry(package=sample_tools_package) -> ToolRegistry:
    return ToolRegistry.discover_tools(ProcessConfig(), SessionConfig(), package=package)


def test_discovers_tools_in_package() -> None:
    registry = _registry()

    names = {tool.name() for tool in registry.tools()}

    assert names == {"echo", "add", "ask_permission", "ask_multi_permission", "compacting"}


def test_instantiate_tool_returns_tool_by_name() -> None:
    registry = _registry()

    echo_tool = registry.instantiate_tool("echo")

    assert echo_tool.apply({"message": "hi"}) == "hi"


def test_instantiate_tool_builds_a_fresh_instance_each_call() -> None:
    registry = _registry()

    assert registry.instantiate_tool("echo") is not registry.instantiate_tool("echo")


def test_tool_definitions_handles_json_schema_parameters() -> None:
    registry = _registry()

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    echo_def = definitions["echo"]
    assert echo_def["type"] == "function"
    assert echo_def["function"]["description"] == "Echoes back the given message."
    assert echo_def["function"]["parameters"]["required"] == ["message"]


def test_tool_definitions_handles_pydantic_parameters() -> None:
    registry = _registry()

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    add_def = definitions["add"]
    assert set(add_def["function"]["parameters"]["properties"]) == {"a", "b"}


def test_default_registry_discovers_production_tools() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    names = {tool.name() for tool in registry.tools()}

    assert "ReadFile" in names
    assert "Bash" in names
    assert "AskUserQuestions" in names
    assert {
        "ListMemories", "SearchMemories", "ReadMemory", "EditMemory", "CreateMemory",
        "ForgetMemory",
    } <= names


def test_tasks_category_is_registered_when_chainlink_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: True)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    names = {tool.name() for tool in registry.tools()}

    assert _TASKS_TOOL_NAMES <= names


def test_tasks_category_is_absent_when_chainlink_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: False)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    names = {tool.name() for tool in registry.tools()}

    assert not (_TASKS_TOOL_NAMES & names)


def test_registry_has_no_session_before_one_is_constructed() -> None:
    registry = _registry()
    assert registry.session is None
    assert registry.instantiate_tool("echo").context.session is None


def test_session_construction_backfills_the_registrys_session_reference(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A ToolRegistry is always built before the Session it's passed into, so the back-reference
    has to be set post-construction by Session.__init__ -- confirm it actually happens, and that
    every ToolSetupContext built afterward carries it."""
    registry = _registry()
    session = Session(config=make_session_config(), tool_registry=registry)

    assert registry.session is session
    assert registry.instantiate_tool("echo").context.session is session


def test_init_builds_a_registry_from_a_subset_of_discovered_classes() -> None:
    """`__init__` takes a `dict[str, type[Tool]]` it clones, so a session-scoped registry can be
    built from a filtered subset of a bootstrap registry's classes without re-scanning a
    package -- the foundation for subagents getting a restricted tool set."""
    bootstrap = _registry()
    subset = {name: cls for name, cls in bootstrap._tool_classes.items() if name in {"echo", "add"}}

    registry = ToolRegistry(ProcessConfig(), SessionConfig(), subset)

    assert {tool.name() for tool in registry.tools()} == {"echo", "add"}
    # The registry cloned the dict, so mutating the caller's copy doesn't affect it.
    subset.clear()
    assert {tool.name() for tool in registry.tools()} == {"echo", "add"}


def test_init_holds_its_own_copy_of_the_tool_classes_dict() -> None:
    """The dict passed to `__init__` is cloned, not retained by reference -- a later change to
    the caller's dict must not surface in the registry."""
    bootstrap = _registry()
    classes = dict(bootstrap._tool_classes)
    registry = ToolRegistry(ProcessConfig(), SessionConfig(), classes)

    classes["bogus"] = bootstrap._tool_classes["echo"]  # type: ignore[index]

    try:
        registry.instantiate_tool("bogus")
    except NoSuchToolException:
        pass
    else:  # pragma: no cover - the registry must not have picked up the caller's mutation
        raise AssertionError("registry should not observe caller-side dict mutations")


def test_alias_map_is_only_self_references_when_no_tools_have_aliases() -> None:
    registry = _registry()
    for (k, v) in registry._alias_map.items():
        assert k == v, f"Got actual alias: {k!r} -> {v!r}"


def test_instantiate_tool_resolves_alias_to_canonical_tool() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    # WriteFile is an alias for CreateFile.
    tool = registry.instantiate_tool("WriteFile")
    assert tool.name() == "CreateFile"


def test_instantiate_tool_resolves_glob_alias_to_find_file() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("Glob")
    assert tool.name() == "FindFile"


def test_instantiate_tool_resolves_tool_search_alias_to_search_tools() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("ToolSearch")
    assert tool.name() == "SearchTools"


def test_instantiate_tool_resolves_write_memory_alias() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("WriteMemory")
    assert tool.name() == "CreateMemory"


@pytest.mark.parametrize(("alias", "canonical"), [
    ("SearchMemory", "SearchMemories"),
    ("ListMemory", "ListMemories"),
    ("ReadMemories", "ReadMemory"),
    ("EditMemories", "EditMemory"),
    ("CreateMemories", "CreateMemory"),
    ("ForgetMemories", "ForgetMemory"),
])
def test_instantiate_tool_resolves_memory_singular_plural_aliases(
    alias: str, canonical: str,
) -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool(alias)
    assert tool.name() == canonical


def test_instantiate_tool_resolves_task_create_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: True)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("TaskCreate")
    assert tool.name() == "TodoCreate"


def test_instantiate_tool_resolves_task_list_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: True)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("TaskList")
    assert tool.name() == "TodoList"


def test_instantiate_tool_resolves_task_next_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: True)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("TaskNext")
    assert tool.name() == "TodoNext"


def test_instantiate_tool_resolves_task_update_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "chainlink_available", lambda: True)
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("TaskUpdate")
    assert tool.name() == "TodoUpdate"


def test_instantiate_tool_still_raises_for_unknown_name() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    try:
        registry.instantiate_tool("BogusTool")
    except NoSuchToolException:
        pass
    else:
        raise AssertionError("expected NoSuchToolException for unknown tool name")


def test_yoda_alias_file_edit() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("FileEdit")
    assert tool.name() == "EditFile"


def test_yoda_alias_file_read() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("FileRead")
    assert tool.name() == "ReadFile"


def test_yoda_alias_file_create() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("FileCreate")
    assert tool.name() == "CreateFile"


def test_yoda_alias_memory_edit() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoryEdit")
    assert tool.name() == "EditMemory"


def test_yoda_alias_memory_read() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoryRead")
    assert tool.name() == "ReadMemory"


def test_yoda_alias_memory_create() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoryCreate")
    assert tool.name() == "CreateMemory"


def test_yoda_alias_scratchpad_edit() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("ScratchpadEdit")
    assert tool.name() == "EditScratchpad"


def test_yoda_alias_scratchpad_read() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("ScratchpadRead")
    assert tool.name() == "ReadScratchpad"


def test_yoda_alias_scratchpad_search() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("ScratchpadSearch")
    assert tool.name() == "SearchScratchpad"


def test_yoda_alias_memories_search() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoriesSearch")
    assert tool.name() == "SearchMemories"


def test_yoda_alias_memories_list() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoriesList")
    assert tool.name() == "ListMemories"


def test_yoda_alias_memory_forget() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("MemoryForget")
    assert tool.name() == "ForgetMemory"


def test_canonical_name_still_works_when_aliases_exist() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    tool = registry.instantiate_tool("CreateFile")
    assert tool.name() == "CreateFile"


def test_default_described_excludes_replace_all_from_tool_definitions() -> None:
    """ReplaceAllTool.default_described() returns False, so it must not appear in the
    tool definitions advertised to the model by default -- even though it stays
    default_visible() (see `test_replace_all_is_visible_but_not_described`)."""
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    definition_names = {d["function"]["name"] for d in registry.tool_definitions()}

    assert "ReplaceAll" not in definition_names
    # Confirm the tool is still in the registry itself (instantiate_tool still works).
    tool = registry.instantiate_tool("ReplaceAll")
    assert tool.name() == "ReplaceAll"


def test_replace_all_is_visible_but_not_described() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    tool = registry.instantiate_tool("ReplaceAll")

    assert tool.default_visible() is True
    assert tool.default_described() is False


def test_extra_visible_tools_includes_non_described_tool_in_definitions() -> None:
    """Setting `extra_visible_tools` on a registry makes those tools appear in
    `tool_definitions()` even when `default_described()` returns `False`."""
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    registry.extra_visible_tools = frozenset({"ReplaceAll"})

    definition_names = {d["function"]["name"] for d in registry.tool_definitions()}

    assert "ReplaceAll" in definition_names


def test_additional_tool_summaries_includes_visible_but_undescribed_tools() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    summaries = {s["name"]: s["description"] for s in registry.additional_tool_summaries()}

    assert "ReplaceAll" in summaries
    assert len(summaries["ReplaceAll"]) <= 80
    assert "ReplaceAll" not in {d["function"]["name"] for d in registry.tool_definitions()}


def test_additional_tool_summaries_excludes_extra_visible_tools() -> None:
    """A tool named in `extra_visible_tools` gets its full schema in `tool_definitions()`
    instead, so it must not also show up in `additional_tool_summaries()`."""
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())
    registry.extra_visible_tools = frozenset({"ReplaceAll"})

    summaries = {s["name"] for s in registry.additional_tool_summaries()}

    assert "ReplaceAll" not in summaries
