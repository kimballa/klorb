# © Copyright 2026 Aaron Kimball
"""Shared file-lookup primitive behind klorb's system-prompt resolution.

`Role.system_prompt()`, `Model.system_prompt()`, and `Session._default_system_prompt()` each
resolve one relative path within a `system_prompts.d/` tree via `resolve_prompt_file()`: a
user-writable override under `$KLORB_CONFIG_DIR/system_prompts.d/`, falling back to the
built-in default shipped inside the `klorb.resources` package. See
docs/specs/roles-and-system-prompts.md for the full resolution order across those three call
sites.
"""

import importlib.resources

from klorb.paths import KLORB_CONFIG_DIR

SYSTEM_PROMPTS_SUBDIR = "system_prompts.d"
"""Name of the `system_prompts.d/` tree, rooted both at `$KLORB_CONFIG_DIR` (user overrides)
and inside the `klorb.resources` package (built-in defaults) — see `resolve_prompt_file()`."""

ROLES_SUBDIR = "roles"
"""Name of the `roles/` subtree within `system_prompts.d/`, holding one directory per
operating role (`roles/<role>/default.md`, `roles/<role>/<mangled model name>.md`) — see
`klorb.role.Role.system_prompt()`."""

DEFAULT_SYS_FILENAME = "default_sys.md"
"""Filename of the role- and model-agnostic default system prompt at the top of a
`system_prompts.d/` tree — see `Session._default_system_prompt()`."""

DEFAULT_SYSTEM_PROMPT = "You are klorb, a helpful coding and software engineering assistant."
"""Last-resort system prompt used only if `default_sys.md` is missing from both the user
override tree and the packaged `klorb.resources` tree — in practice this never triggers,
since the packaged copy always ships with klorb (see `resources/system_prompts.d/default_sys.md`)."""


def mangle_model_name(model_name: str) -> str:
    """Turn a model identifier (e.g. `"poolside/laguna-m.1:free"`) into a filesystem-safe,
    collision-free filename stem by replacing `/` and `:` with `__`.

    Model identifiers are already vendor-qualified (`<vendor>/<model>[:<variant>]`), so this
    mangling alone is enough to keep filenames unique without needing a separate
    provider-name directory tier.
    """
    return model_name.replace("/", "__").replace(":", "__")


def resolve_prompt_file(relative_path: str) -> str | None:
    """Return the contents of `relative_path` within `system_prompts.d/`, or `None` if it
    exists in neither tier.

    Checks, in order: the user override at
    `$KLORB_CONFIG_DIR/system_prompts.d/<relative_path>`, then the built-in default packaged
    at `klorb.resources/system_prompts.d/<relative_path>`.
    """
    user_path = KLORB_CONFIG_DIR / SYSTEM_PROMPTS_SUBDIR / relative_path
    if user_path.is_file():
        return user_path.read_text()

    packaged_path = (
        importlib.resources.files("klorb.resources")
        .joinpath(SYSTEM_PROMPTS_SUBDIR)
        .joinpath(relative_path)
    )
    if packaged_path.is_file():
        return packaged_path.read_text()

    return None
