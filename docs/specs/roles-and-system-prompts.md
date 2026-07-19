# Roles and system prompts

## Summary

klorb's system prompts live in markdown files, not Python string literals, resolved along
two independent axes of specificity — the session's operating *role* (what job the agent is
performing: coordinating a task, exploring a codebase, auditing a change, ...) and the
active *model* — across two tiers: a user-editable override tree under
`$KLORB_CONFIG_DIR/system_prompts.d/`, and a built-in tree shipped inside the installed
`klorb.resources` package. A `Role` object (`klorb/src/klorb/role.py`) represents the
operating role; `Session` builds its own `Role` from `SessionConfig.role_name` and, on every
turn, resolves a role-and-model-agnostic default prompt and a role-specific prompt as two
independent walks, then concatenates them. Roles exist because coding is headed toward a
multi-agent exercise: two agents on the *same* model doing different jobs (writing code vs.
auditing it) need different instructions, so role — not model, and not the API provider the
model is reached through — is the primary axis a prompt hangs on. See
[[resolve-system-prompts-role-first-then-model-then-default]],
[[concatenate-role-prompt-onto-default-instead-of-replacing-it]], and
[[ship-system-prompts-as-package-data-with-user-config-overrides]].

## How it works

### The prompt file tree

Both tiers share one layout, rooted at a `system_prompts.d/` directory:

```text
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
  `roles/operator/default.md` today.
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
  expected to be filesystem-safe slugs (`operator`).

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
* `OperatorRole` is the default top-level role: the lead agent that owns a coding task
  end to end, with full latitude to research, decide, plan, write docs/code/tests, run and
  debug, and review work (its own or another agent's), biased toward an iterative
  research/think/decide/plan/execute/verify/analyze loop and toward decomposing large
  problems into ordered fine-grained tasks. Those behavioral instructions live in
  `resources/system_prompts.d/roles/operator/default.md`, not in code — the class itself
  only supplies the name.
* `NamedRole` covers any `role_name` with no dedicated subclass: it carries the string
  as-is and triangulates behavior purely from the prompt-file naming convention (whatever
  `roles/<name>/` files exist, layered onto the model and default tiers per the resolution
  below — or, if none exist, no `<AgentRole>` addendum at all).
* `get_role(role_name: str) -> Role` is the factory: dedicated subclass when one exists
  (today only `"operator"` → `OperatorRole`), else `NamedRole(role_name)`.
* `SessionConfig.role_name` (default `OPERATOR_ROLE_NAME`) is the only way a role enters
  a session: `Session.__init__` calls `get_role(config.role_name)` itself and exposes the
  result as the `Session.role` property, so a caller can never construct a session whose
  `Role` disagrees with its `config.role_name`. `role_name` is set by code — the default,
  or a future subagent-spawning call site — and is deliberately *not* a recognized
  `klorb-config.json` key (absent from `SESSION_KEY_MAP`; see
  [[process-and-session-config]]), so a config file can't change what kind of agent the
  user is talking to.

### Resolution order

`Session._resolve_system_prompt() -> str` picks the prompt for the active turn by running
two independent resolver walks and concatenating their results, rather than a single chain
where the first hit wins. Within each walk, the user tier beats the packaged tier at every
step (that tie break lives inside `resolve_prompt_file`); each method in either walk returns
`str | None` and `None` means "fall through to the next tier of *this* walk," so test
fixtures (e.g. `klorb/tests/fixtures/sample_models/*.py`) can override
`Model.system_prompt()`/`Role.system_prompt()` to return literal strings with no filesystem
access.

* The **default walk** is role-agnostic and never comes up empty:
  1. `<mangled-model>.md` — via `Model.system_prompt()`, skipped when `config.model` has no
     registered `Model` ([[model-framework]])
  2. `default_sys.md` — via `Session._default_system_prompt()`
  3. `klorb.system_prompts.DEFAULT_SYSTEM_PROMPT`, a hardcoded constant — a safety net that
     never triggers in practice, since the packaged `default_sys.md` always ships
* The **role walk** may come up empty (`None`):
  1. `roles/<role>/<mangled-model>.md` — via `Role.system_prompt(model)`
  2. `roles/<role>/default.md` — same call

The default walk's result is always the base of the final prompt. When the role walk also
produces a prompt, it's wrapped in an `<AgentRole>...</AgentRole>` tag (`_wrap_agent_role`)
and appended after the default prompt, separated by a blank line — so a role's instructions
*layer onto* the default ones rather than replacing them. When the role walk resolves
nothing, the default walk's result is returned as-is, with no `<AgentRole>` block at all.
This means every session's prompt always includes `default_sys.md` (or its user override),
regardless of role — there is no way for a role to opt out of the role-and-model-agnostic
default prompt, only to add to it.

Context-instruction files (`AGENTS.md`, `.klorb/INSTRUCTIONS.md`, `CLAUDE.md`) are no part
of this resolution at all: they're gathered by `_build_context_files_interjection()` and
prepended, wrapped in a `<SystemInterjection subject="ProjectGuidance">` tag, onto the
first turn's *user* message by `send_turn()` — a distinct mechanism from the system prompt
described here (see [[workspace-context-files]]). Positionally this guidance still lands
after the system prompt resolved here, since the system prompt is sent as its own field
ahead of the conversation's messages.

Because `default_sys.md` is now unconditionally part of every session's prompt,
`roles/operator/default.md` no longer needs to restate `default_sys.md`'s general
engineering discipline (grounding, minimal diffs, verification, honest reporting) to ensure
an operator session sees it — both apply together. `roles/operator/default.md` is
expected to hold only the operator's own process-leadership instructions layered on top
(task ownership, the research/think/decide/plan/execute/verify/analyze loop, problem
decomposition), not a copy of the default prompt's material.

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
