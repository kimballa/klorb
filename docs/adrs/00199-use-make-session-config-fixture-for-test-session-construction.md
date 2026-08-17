2026-08-16

## Question

Tests that create `Session` objects need a `SessionConfig` with sensible defaults --
especially a `workspace` rooted at the test's own `tmp_path`. How should tests construct
these, and how should future features opt out of or reconfigure behavior specifically for
unit tests?

## Answer

Every test that needs a `SessionConfig` uses the `make_session_config` pytest fixture
(defined in `klorb/tests/conftest.py`). It returns a factory function that creates a
`SessionConfig` with `workspace` rooted at the test's `tmp_path` by default. Any field,
including `workspace` itself, can be overridden via keyword arguments:

```python
def test_something(make_session_config: Callable[..., SessionConfig]) -> None:
    session = Session(make_session_config(), provider=MagicMock())
    session = Session(make_session_config(role_name="explorer"), provider=MagicMock())
```

`SessionConfig(...)` construction was removed from all tests in a bulk refactor. Tests
must not construct `SessionConfig` directly.

When a future feature needs non-default behavior in tests, its test-friendly configuration
should be added as a new parameter to `make_session_config()` -- which means adding a
matching field to `SessionConfig` and a sensible-for-tests override in the factory. This
replaces the pattern of autouse fixtures that short-circuit feature functionality by
monkeypatching internals.

## Reasoning

Direct `SessionConfig()` construction scattered across tests produced two problems:
duplication (every test restates `workspace=Workspace(path=tmp_path)`) and fragility (adding
a required field means touching every call site). A shared fixture centralizes the
boilerplate and makes adding a new field a one-line change.

The deeper payoff is in how test-specific feature overrides are managed. Previously, the
conventional pattern for disabling or reconfiguring a feature in tests was an autouse
fixture that monkeypatches internal methods -- invisible to the code under test, hard to
discover, and easy to forget when the internals change. With `make_session_config()` as the
single construction point, a feature that needs a test-friendly setting (a disabled flag, a
mock-friendly endpoint, a shortened timeout) can express it as a `SessionConfig` field with
a default value that the factory overrides for tests. The production code reads the
configuration normally; no monkeypatching required.
