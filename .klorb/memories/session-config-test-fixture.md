Creating Session instances in python unit tests

When a test needs a `Session` (or just a `SessionConfig`), use the `make_session_config`
pytest fixture defined in `klorb/tests/conftest.py`. It returns a factory function:

```python
def test_foo(make_session_config: Callable[..., SessionConfig]) -> None:
    session = Session(make_session_config(), provider=MagicMock())
```

The factory defaults `workspace` to `Workspace(path=tmp_path)` (the test's own temp dir).
Any `SessionConfig` field can be overridden via keyword: `make_session_config(role_name="explorer")`.

Do not construct `SessionConfig(...)` directly in tests. If a future feature needs a
test-specific override, add a field to `SessionConfig` and a sensible default to
`make_session_config()` instead of using a monkeypatch autouse fixture. See ADR
00199 for the full rationale.