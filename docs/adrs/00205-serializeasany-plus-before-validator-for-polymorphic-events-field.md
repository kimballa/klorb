# © Copyright 2026 Aaron Kimball

**Date:** 2026-08-20

**Question:** `SessionConfig.events: dict[str, list[EventConfig]]` holds `EventConfig` subclass
instances (`FileSystemModifiedEventConfig`, `TimerEventConfig`, ...) keyed by event name, but a
session's `model_dump()`/`model_validate()` round trip through `session.json`
(`klorb.workspace.session_store.write_session_state`/`read_session_state`) silently collapsed
every entry to the base `EventConfig` shape, dropping fields like `watch`/`interval_minutes` and
crashing `FileSystemWatcher`/`TimerScheduler` with an `AttributeError` on the next session
restore. How should a dict field typed by a base class, but holding named subclasses, survive a
generic pydantic JSON round trip?

**Answer:** Annotate the field as `dict[str, list[SerializeAsAny[EventConfig]]]` so
`model_dump()` serializes each entry's actual runtime type instead of `EventConfig`'s own schema,
and add a `field_validator("events", mode="before")` that re-resolves each entry to its
`EVENT_CONFIG_MODELS[name]` subclass before pydantic's normal validation runs.

**Reasoning:** `klorb.process_config.load_process_config()` already builds `events` correctly by
hand-dispatching each name through `EVENT_CONFIG_MODELS` (`hooks/merge.py`'s
`parse_handler_list`), so a freshly started process never hit this. Pydantic v2's default
behavior for a base-class-typed field is to serialize and validate against the declared type, not
the instance's own subclass, unless told otherwise — a well-documented but easy-to-miss gotcha.
`SerializeAsAny` alone fixes serialization but not deserialization, since a bare `EventConfig`
type still has no way to know which subclass a given dict key implies; the `mode="before"`
validator supplies that by reusing the same `EVENT_CONFIG_MODELS` name-to-model mapping the
initial config load already relies on, so both directions resolve the same way.
