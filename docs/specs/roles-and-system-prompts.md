# Roles and system prompts

## Summary

klorb's system prompts live in markdown files, not Python string literals, resolved along
two independent axes of specificity — the session's operating *role* (what job the agent is
performing: coordinating a task, exploring a codebase, auditing a change, ...) and the
active *model* — across two tiers: a user-editable override tree under
`$KLORB_CONFIG_DIR/system_prompts.d/`, and a built-in tree shipped inside the installed
`klorb.resources` package. A `Role` object (`klorb/src/klorb/role.py`) represents the
operating role; `Session` builds its own `Role` from `SessionConfig.role_name` and resolves
the prompt most-specific-source-first on every turn. Roles exist because coding is headed
toward a multi-agent exercise: two agents on the *same* model doing different jobs (writing
code vs. auditing it) need different instructions, so role — not model, and not the API
provider the model is reached through — is the primary axis a prompt hangs on. See
[[resolve-system-prompts-role-first-then-model-then-default]] and
[[ship-system-prompts-as-package-data-with-user-config-overrides]].

## How it works

### The prompt file tree

Both tiers share one layout, rooted at a `system_prompts.d/` directory:

```
system_prompts.d/
  default_sys.md            # role- and model-agnostic default
  <mangled-model>.md        # role-agnostic, model-tuned override
  roles/
    <role>/
      default.md            # this role's base prompt, any model
      <mangled-model>.md    # this role's prompt, tuned for one model
```

* The **user tier** is rooted at `$KLORB_CONFIG_DIR/system_prompts.d/` (defaults to
  `~/.config/klorb/system_prompts.d/` — see [[paths-and-logging]]). Files here are created
  by the user (or, eventually, by the dump command described in `TODO.md`); nothing
  installs them.
* The **packaged tier** is rooted inside the installed package, at
  `klorb/src/klorb/resources/system_prompts.d/` in the source tree, declared as
  `[tool.setuptools.package-data]` in `klorb/pyproject.toml` and read at runtime via
  `importlib.resources.files("klorb.resources")` — so it's present for a pip-installed
  user, not just a source checkout. It ships `default_sys.md` and
  `roles/coordinator/default.md` today.
* `klorb.system_prompts.resolve_prompt_file(relative_path) -> str | None`
  (`klorb/src/klorb/system_prompts.py`) is the single primitive every lookup goes through:
  it returns the file's contents from the user tier if present there, else from the
  packaged tier, else `None`. The "user overrides packaged" rule therefore exists in
  exactly one place.
* Model identifiers contain characters unsafe in filenames (`/`, `:` — e.g.
  `poolside/laguna-m.1:free`), so a model's filename stem is its **mangled name**:
  `mangle_model_name()` replaces both characters with `__`
  (`poolside__laguna-m.1__free.md`), exposed per-model as `Model.mangled_name()`. Model
  identifiers are already vendor-qualified, so this alone keeps stems collision-free with
  no per-provider directory. Role names are used as directory names directly and are
  expected to be filesystem-safe slugs (`coordinator`).

There is deliberately no *provider* axis anywhere in the tree: a model is the same model
regardless of which API gateway serves it, so a prompt tuned for it applies either way — a
provider-specific quirk severe enough to need different prompt text would just be a
model-specific override like any other.

### Roles

* `klorb.role.Role` is an abstract base class. `name()` (abstract) returns the role's
  identifier — the `SessionConfig.role_name` string it was built from, and the directory
  its prompt files live under. `system_prompt(model: Model | None) -> str | None`
  (concrete) resolves the role-specific tiers: `roles/<name>/<mangled-model>.md` (skipped
  when `model` is `None`), then `roles/<name>/default.md`, returning `None` when neither
  file exists in either tier. `repertoire() -> list[str]` (concrete, always empty today) is
  the placeholder hook for the specialist subagents and role-specific tools a role will
  eventually be able to employ.
* `CoordinatorRole` is the default top-level role: the lead agent that owns a coding task
  end to end, with full latitude to research, decide, plan, write docs/code/tests, run and
  debug, and review work (its own or another agent's), biased toward an iterative
  research/think/decide/plan/execute/verify/analyze loop and toward decomposing large
  problems into ordered fine-grained tasks. Those behavioral instructions live in
  `resources/system_prompts.d/roles/coordinator/default.md`, not in code — the class itself
  only supplies the name.
* `NamedRole` covers any `role_name` with no dedicated subclass: it carries the string
  as-is and triangulates behavior purely from the prompt-file naming convention (whatever
  `roles/<name>/` files exist, else the resolution below falls through to the model and
  default tiers).
* `get_role(role_name: str) -> Role` is the factory: dedicated subclass when one exists
  (today only `"coordinator"` → `CoordinatorRole`), else `NamedRole(role_name)`.
* `SessionConfig.role_name` (default `COORDINATOR_ROLE_NAME`) is the only way a role enters
  a session: `Session.__init__` calls `get_role(config.role_name)` itself and exposes the
  result as the `Session.role` property, so a caller can never construct a session whose
  `Role` disagrees with its `config.role_name`. `role_name` is set by code — the default,
  or a future subagent-spawning call site — and is deliberately *not* a recognized
  `klorb-config.json` key (absent from `SESSION_KEY_MAP`; see
  [[process-and-session-config]]), so a config file can't change what kind of agent the
  user is talking to.

### Resolution order

`Session._resolve_system_prompt() -> str` picks the prompt for the active turn, most
specific source first; within each source, the user tier beats the packaged tier (that tie
break lives inside `resolve_prompt_file`):

1. `roles/<role>/<mangled-model>.md` — via `Role.system_prompt(model)`
2. `roles/<role>/default.md` — same call
3. `<mangled-model>.md` — via `Model.system_prompt()`, skipped when `config.model` has no
   registered `Model` ([[model-framework]])
4. `default_sys.md` — via `Session._default_system_prompt()`
5. `klorb.system_prompts.DEFAULT_SYSTEM_PROMPT`, a hardcoded constant — a safety net that
   never triggers in practice, since the packaged `default_sys.md` always ships

Every method in the chain returns `str | None` and `None` means "fall through", so test
fixtures (e.g. `klorb/tests/fixtures/sample_models/*.py`) can override
`Model.system_prompt()`/`Role.system_prompt()` to return literal strings with no
filesystem access. Note the consequence of role outranking model: while a
`roles/coordinator/default.md` ships in the package, a top-level `<mangled-model>.md` is
never reached by a coordinator session — model tuning *for the coordinator* belongs at
`roles/coordinator/<mangled-model>.md`, and the top-level model files serve sessions whose
role resolved no files at all.

Because the coordinator's role prompt must stand alone (tier 2 wins outright; tiers below
it are never appended), `roles/coordinator/default.md` repeats the general engineering
discipline of `default_sys.md` (grounding, minimal diffs, verification, honest reporting)
and adds the coordinator's process-leadership instructions on top. The two files are
deliberately separate rather than one shared prompt: `default_sys.md` backstops *every*
session that resolves no role file — including future specialist subagents whose role files
don't exist yet — so it must stay role-neutral rather than impose the coordinator's
decompose-and-orchestrate bias on, say, an explore subagent that fell through to it.

`Session` re-resolves the prompt fresh at each use — the `role="system"` bookkeeping
message inserted before the first turn ([[store-system-prompt-as-a-bookkeeping-message]])
and the live `send_prompt(system_prompt=...)` argument on every turn ([[session-and-turns]])
— so a mid-session `config.model` change is reflected on the next turn. Since resolution
always produces a prompt, every session sends one, including sessions on unregistered model
strings; the bookkeeping system message is likewise always inserted.

## Configuration

* `$KLORB_CONFIG_DIR/system_prompts.d/` — the user override tree (see "How it works").
  There is no `klorb-config.json` key for any of this: prompt selection is driven entirely
  by which files exist.
* `SessionConfig.role_name` — code-settable only; not a config-file key, and no CLI flag
  exists for it today.

## Out of scope

* **Subagent spawning / agent teams.** The multi-agent design roles exist to serve — a
  spawned subagent inheriting a copy of the parent's session config with `role_name`
  swapped to its specialty, plus a parent-provided instructions message — is described in
  `TODO.md` and not built. `Role.repertoire()` and the `Role` subclass seam
  (`CodingRole`, `ExploreRole`, `AuditorRole`, ...) are the placeholders.
* **Role-specific tool access.** A role will eventually constrain or extend which tools a
  session offers; today `Role` carries no tool information.
* **The prompt-dump command** (`TODO.md`): materializing the resolved prompt into the user
  tree as an editable starting point.
* **Mid-session role switching.** `Session.role` is built once in `__init__`; nothing
  mutates `config.role_name` afterward, and no palette command exists for it.
* **`Model.settings()`** remains unwired into requests, as before ([[model-framework]]).
