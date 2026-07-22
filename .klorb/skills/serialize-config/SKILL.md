---
name: serialize-config
description: How to serialize klorb-config.json configuration files properly using write_versioned_json. Use whenever you need to write, update, or persist a klorb-config.json configuration file.
---

# Serialize klorb-config.json files

Use this skill whenever you need to write, update, or persist a `klorb-config.json` configuration file.

## Overview

`klorb-config.json` files use a schema envelope with specific formatting requirements. The codebase provides `write_versioned_json()` from `klorb.schema_envelope` as the canonical way to serialize these files.

## Key Principles

1. **Always use `write_versioned_json()`** - Never use `json.dumps()` directly for config files
2. **Preserve existing keys** - Read the current file first, update only what needs to change
3. **Atomic writes** - `write_versioned_json()` handles atomic file replacement automatically

## Usage Patterns

### For process-level config keys (outside `sessionDefaults`)

Use `persist_task_sidebar()` or similar helper functions that wrap `write_versioned_json()`:

```python
from klorb.process_config import persist_task_sidebar

# Persist a process-level setting
persist_task_sidebar(True)
```

### For session-scoped config keys (inside `sessionDefaults`)

Use `persist_session_default()`:

```python
from klorb.process_config import persist_session_default, user_config_path

# Persist a session default setting
persist_session_default(user_config_path(), "model", "new/model")
```

### For custom config persistence

If you need to write a new config key, follow this pattern:

```python
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    CONFIG_SCHEMA_VERSION,
    read_versioned_json,
    write_versioned_json,
    user_config_path
)

def persist_my_setting(value: Any) -> None:
    """Write my_setting to the user config file."""
    path = user_config_path()
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    new_contents = dict(raw)
    new_contents["my.setting"] = value
    write_versioned_json(
        path,
        new_contents,
        schema_name=CONFIG_SCHEMA_NAME,
        schema_version=CONFIG_SCHEMA_VERSION
    )
```

## What `write_versioned_json()` Handles

- Adds the `schema: {name, version}` envelope automatically
- Creates parent directories if they don't exist
- Pretty-prints with 2-space indentation
- Handles compact formatting for permission-rule lists (arrays of arrays)
- Atomic writes (temp file + rename) to prevent corruption

## Key Naming Convention

Config file keys use dot-delineated, lowerCamelCase namespacing:
- Process-level: `ui.theme`, `ui.taskSidebar.visible`, `shell.command`
- Session defaults: `model`, `thinking.effort`, `tools.maxCallsPerTurn`

## Testing

When writing tests for config persistence:

1. Use `_write_config()` helper from tests to create test files
2. Test file creation, key preservation, and round-trip loading
3. Verify the schema envelope is correct

```python
def test_my_persist_function(tmp_path: Path) -> None:
    path = tmp_path / "klorb-config.json"
    persist_my_setting(path, "value")

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["my.setting"] == "value"
    assert written["schema"] == {"name": "klorb-config", "version": "1.0.0"}
```

## Common Mistakes

- ❌ Using `json.dumps()` directly - loses schema envelope and formatting
- ❌ Not reading existing file first - overwrites all other settings
- ❌ Using wrong key naming (snake_case instead of lowerCamelCase)
- ❌ Not importing `CONFIG_SCHEMA_NAME` and `CONFIG_SCHEMA_VERSION`

## Related Files

- `klorb/src/klorb/schema_envelope.py` - Core serialization functions
- `klorb/src/klorb/process_config.py` - Config persistence helpers
- `klorb/tests/klorb/test_process_config.py` - Test examples
