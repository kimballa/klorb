# PLAN-014: Replace the permission-ask "union of optional fields" with a `PermissionResource` hierarchy

## Problem

`PermissionAskItem` (`klorb/src/klorb/permissions/table.py`) and `PermissionAskContext`
(`klorb/src/klorb/session/events.py`) each carry one optional field per resource kind —
`path`/`command`/`skill`/`url` — with the invariant "exactly one is set, or none," enforced only
by convention and docstring prose, never by the type system. Every consumer that receives one of
these objects (the TUI panel, the session grant-resolution mixin, the risk classifier) has to
re-derive "which kind is this" via a chain of `is not None` checks, and adding a new resource kind
means finding and updating every one of those chains by hand — one is already missing today:
`PermissionAskPanel` has no `url`/domain branch at all, so a `WebFetch` permission ask currently
shows a bare "Confirm" header with no URL preview.

## Solution

Replace the flat field bag with a small `PermissionResource` class hierarchy — one concrete
subclass per permission space (paths, commands, skills, domains) plus a `Structural` case for a
BashTool item the shell walker couldn't classify — where each subclass owns its own display text,
grant-preview computation, grant application, and once-scope override handling. Consumers stop
branching on "which field is set" and instead call one polymorphic method on whichever concrete
resource they have. This also fixes the missing domain-ask preview as a byproduct of giving every
kind uniform treatment.

### Consumer map

* **Construction of `PermissionAskItem`**: `klorb/src/klorb/tools/bash.py` — the only constructor
  call sites (one per command item, structural item, and path/redirect item); skill/domain items
  never flow through `PermissionAskItem`/`MultiPermissionAskRequired` at all, only through the
  single-item `PermissionAskRequired`.
* **Construction of `PermissionAskContext`**: `klorb/src/klorb/session/mixins/tool_execution.py`
  (from a caught `PermissionAskRequired`) and
  `klorb/src/klorb/session/mixins/permissions.py`'s `_resolve_multi_permission_ask` (copying one
  `PermissionAskItem`'s fields).
* **Kind-dispatch (`is not None` chains) to fix**:
  `klorb/src/klorb/session/mixins/permissions.py` (`_retry_after_permission_decision`,
  `_apply_ask_grant`, `_retry_after_multi_permission_decisions`),
  `klorb/src/klorb/session/mixins/tool_execution.py` (`has_resource` check),
  `klorb/src/klorb/tui/panels/permission_ask_panel.py` (`header_text`, `compose`,
  `_granted_text`, `format_ask_context_body`, `action_expand_command`),
  `klorb/src/klorb/tui/mixins/interactions.py` (`_confirm_permission_ask`).
* **The four existing grant modules stay as the low-level implementation**, unchanged in their own
  internals: `klorb/src/klorb/permissions/grant.py` (paths — two-table, canonicalized, not built
  on the shared `RuleGrantWriter` base), `command_grant.py`, `skill_grant.py`, `domain_grant.py`
  (all three single-table, built on `klorb.permissions.rule_grant_base.RuleGrantWriter`). The new
  `PermissionResource` subclasses are thin polymorphic wrappers that call into these, so none of
  the four modules' own file-persistence logic needs to change.
* **`PermissionOverride`** (5 parallel `frozenset` fields — `paths`/`commands`/`reasons`/`skills`/
  `domains`) keeps its current shape: it's a legitimate "bag of independent bypass criteria" (a
  single compound bash call can populate several fields at once), not a mutual-exclusion union, so
  it isn't part of the problem this plan fixes. What changes is *how it's read and built* — every
  scattered `x in override.<field>` membership check and the `once_paths`/`once_commands`/
  `once_reasons`/`once_skills` manual-bucketing dance in `_retry_after_multi_permission_decisions`
  become one polymorphic method on `PermissionResource` instead. `PermissionOverride` itself
  relocates into the new `klorb/src/klorb/permissions/resource.py` module (out of `table.py`),
  since `resource.py`'s own methods are what construct it and `table.py` needs to import
  `PermissionResource` from `resource.py` — keeping `PermissionOverride` in `table.py` would make
  that a circular import.
* **Tests to update**: `klorb/tests/klorb/permissions/test_permissions.py`, `test_grant.py`,
  `test_command_grant.py`, `test_skill_grant.py`, `test_risk_classifier.py`,
  `klorb/tests/klorb/session/test_session.py`, `klorb/tests/klorb/session/mixins/test_skills.py`,
  `klorb/tests/klorb/tools/test_bash.py`,
  `klorb/tests/klorb/tui/mixins/test_interactions.py`,
  `klorb/tests/klorb/tui/panels/test_permission_ask_panel.py`, `klorb/tests/klorb/tui/test_app.py`,
  `klorb/tests/klorb/tui/conftest.py`, `klorb/tests/fixtures/sample_tools/ask_multi_permission_tool.py`.
  A new `klorb/tests/klorb/permissions/test_resource.py` covers the new hierarchy directly. Every
  production file that imports `PermissionOverride` from `klorb.permissions.table` (`klorb/src/
  klorb/tools/skill/catalog.py`, `common.py`, `klorb/src/klorb/tools/registry.py`,
  `setup_context.py`, plus their own test files) needs that import repointed at
  `klorb.permissions.resource`.

## New module: `klorb/src/klorb/permissions/resource.py`

```python
class PermissionResource(ABC):
    @abstractmethod
    def header_kind(self) -> str: ...
    # "Run command" / "Read file" / "Write file" / "Activate skill" / "Fetch URL" / "Confirm"

    @abstractmethod
    def preview_text(self) -> str | None: ...
    # The path / "/name (namespace)" / URL shown as PermissionAskPanel's resource preview line.
    # None for CommandResource (previewed via BashCommandContext instead) and StructuralResource
    # (nothing to preview).

    @property
    def is_persistable(self) -> bool:
        return True  # False for StructuralResource: no rule a grant can be recorded against.

    @abstractmethod
    def grant_preview(self, session_config: SessionConfig) -> GrantPreview | None: ...
    # What a persistent grant for this resource would actually cover, for a UI to show up front.
    # None only for StructuralResource, which has nothing persistable to preview.

    @abstractmethod
    def apply_grant(
        self, action: GrantAction, scope: GrantScope,
        session_config: SessionConfig, process_config: ProcessConfig | None,
        *, grant_patterns: list[list[str]] | None = None,
    ) -> None: ...
    # Persists action/scope for this resource. grant_patterns is meaningful only for
    # CommandResource -- PermissionDecision.grant_patterns threaded straight through so a
    # risk-classifier-suggested wildcard the UI displayed is exactly what's persisted; every
    # other kind ignores it. A no-op for StructuralResource.

    @abstractmethod
    def added_to_override(self, override: PermissionOverride) -> PermissionOverride: ...
    # A new PermissionOverride with this resource added to whichever of its five fields matches
    # this resource's own kind, leaving every other field as `override` already had it.


@dataclass(frozen=True)
class GrantPreview:
    resource_text: str
    block: bool = False  # PathResource only: resource text renders on its own line
```

Concrete subclasses (each `@dataclass(frozen=True)`, hashable where their identity fields allow
it — `argv`/`skill_id` as tuples, matching `PermissionOverride`'s own frozenset element types):

* `PathResource(path: Path, is_write: bool = False)` — wraps `grant.py`'s
  `compute_grant_paths`/`apply_permission_grant`; override key is `path`.
* `CommandResource(argv: tuple[str, ...])` — wraps `command_grant.py`'s
  `compute_command_grant_patterns`/`apply_command_permission_grant`; override key is `argv`.
* `SkillResource(skill_id: tuple[str, str])` — wraps `skill_grant.py`'s
  `apply_skill_permission_grant`; `grant_preview` just previews itself (no widening); override
  key is `skill_id`.
* `DomainResource(url: str)` — carries the full URL (for display, matching what
  `PermissionAskRequired.url` already means) and derives `domain` via
  `klorb.permissions.domain_access.parse_domain(url)` for grant/override purposes (matching
  `domain_grant.py`/`PermissionOverride.domains`, which are domain-keyed, not URL-keyed). This is
  the new domain-ask preview support that today's `PermissionAskPanel` is missing.
* `StructuralResource(reason: str)` — `header_kind()` returns `"Confirm"`, `preview_text()` and
  `grant_preview()` return `None`, `is_persistable` is `False`, `apply_grant()` is a no-op;
  override key is `reason` (the deterministic forced-ask reason text, matching
  `PermissionOverride.reasons`' existing semantics).

Also in this module: `BashCommandContext`, a small frozen dataclass bundling `command_text: str`
(required), `is_compound: bool = False`, `item_command_text: str | None = None`, and
`intent: str | None = None` — replacing those four fields that were spread across
`PermissionAskItem`/`PermissionAskContext` independently of resource kind. `command_text`/
`is_compound` are always real whenever `bash_context` is set at all (every `BashTool`-originated
ask sets them); `item_command_text`/`intent` stay optional even then, since `PermissionAskPanel`'s
own display logic already has an independent fallback/omission behavior for each (falls back to
`command_text` when `item_command_text` is unset; omits the "Intent: ..." line entirely when
`intent` is unset) that existing tests exercise directly.

`PermissionOverride` also moves into this module (see "Consumer map" above for why).

## Changes to existing files

**`klorb/src/klorb/permissions/table.py`**

* `PermissionAskItem.__init__` takes `resource: PermissionResource` (no longer optional —
  structural items pass `StructuralResource(reason=resource_description)`) and
  `bash_context: BashCommandContext | None = None`, replacing `path`/`is_write`/`command`/`skill`/
  `command_text`/`is_compound`/`item_command_text`/`intent`.
* `PermissionAskRequired` keeps its existing `path`/`is_write`/`skill`/`url` constructor kwargs
  unchanged (so none of the ~19 `raise_if_not_allowed(...)` call sites across
  `klorb/src/klorb/tools/*.py` need to change), but gains a `resource: PermissionResource`
  attribute computed internally from those same kwargs (`PathResource` if `path` given, else
  `SkillResource` if `skill` given, else `DomainResource` if `url` given, else
  `StructuralResource(reason=message)` as the fallback). `raise_if_not_allowed` itself is
  otherwise unchanged.
* `PermissionOverride` moves out to `klorb.permissions.resource` (see above).

**`klorb/src/klorb/session/events.py`**

* `PermissionAskContext`: replace `path`/`is_write`/`command`/`command_text`/`is_compound`/
  `item_command_text`/`intent`/`skill`/`url` with `resource: PermissionResource` and
  `bash_context: BashCommandContext | None = None`. Keep `resource_description: str` and
  `sibling_items: list[PermissionAskItem] | None = None`.
* `PermissionDecision`: unchanged (already has `action`/`scope`/`other_text`/`grant_patterns`).

**`klorb/src/klorb/session/mixins/tool_execution.py`**

* Replace `has_resource = path is not None or skill is not None or url is not None` with
  `ask_exc.resource.is_persistable` (inverted sense) as the fail-closed check for a single-item
  ask.
* Replace the `PermissionAskContext(path=..., is_write=..., skill=..., url=..., ...)` construction
  with `PermissionAskContext(resource=ask_exc.resource, resource_description=str(ask_exc))`.

**`klorb/src/klorb/session/mixins/permissions.py`**

* `_retry_after_permission_decision`: replace the `assert ask_exc.path is not None or ...` with
  `assert ask_exc.resource.is_persistable`; replace the
  skill/url/path `PermissionOverride(...)` construction with
  `ask_exc.resource.added_to_override(PermissionOverride())`.
* `_apply_ask_grant`: collapses to `ask_exc.resource.apply_grant(action, scope, self.config, self._process_config)`.
* `_retry_after_multi_permission_decisions`: replace the `once_paths`/`once_commands`/
  `once_reasons`/`once_skills` bucketing with an accumulator built via
  `item.resource.added_to_override(...)`; replace the path/command/skill grant-dispatch branches
  with `item.resource.apply_grant(decision.action, decision.scope, self.config, self._process_config, grant_patterns=decision.grant_patterns)` (structural items no-op automatically).
* `_resolve_multi_permission_ask`: replace the manual field-copy `PermissionAskContext(...)` with
  `PermissionAskContext(resource=item.resource, resource_description=item.resource_description, bash_context=item.bash_context, sibling_items=multi_ask_exc.items)`.

**`klorb/src/klorb/tools/bash.py`**

* The three `PermissionAskItem(...)` construction sites switch to passing
  `resource=CommandResource(argv=tuple(argv))`, `resource=StructuralResource(reason=forced_reason.reason)`, `resource=PathResource(path=path, is_write=is_write)` respectively, each with
  `bash_context=BashCommandContext(command_text=command, is_compound=is_compound, item_command_text=..., intent=intent)`.

**`klorb/src/klorb/tui/panels/permission_ask_panel.py`**

* `header_text()`: "Run command" whenever `ask_ctx.bash_context` is set, regardless of the
  specific resource within it (a redirect or forced-ask item from `BashTool` is still
  fundamentally "run this shell command," just with a specific sub-resource named in the detail
  below — this matches today's behavior, which keys off `command_text` being set rather than the
  item's specific kind); otherwise `ask_ctx.resource.header_kind()`.
* `compose()`'s preview-section branch: check `bash_context is not None` first (the
  command-preview-with-truncation/[more...] path, with its existing item_command_text-falls-back-
  to-command_text and intent-shown-only-if-set behavior preserved); otherwise fall back to
  `resource.preview_text()` — this naturally adds the missing domain/URL preview line.
* `_granted_text()`: replace the `granted_paths`/`granted_command_patterns`/`granted_skill`
  three-way dispatch with one `self._granted_preview: GrantPreview | None` constructor param,
  rendering the same two prose variants (`block` vs. inline) driven by `GrantPreview.block`
  instead of by which of three fields was truthy. This also naturally covers domain grants, which
  currently render no "grants: ..." line at all.
* `format_ask_context_body()`: same `bash_context`-first, `resource.preview_text()`-fallback
  pattern as `compose()`.
* `action_expand_command()`: gate on `self._ask_ctx.bash_context is not None`, using
  `bash_context.command_text`.
* Constructor: replace `granted_paths`/`granted_command_patterns`/`granted_skill` params with
  `granted_preview: GrantPreview | None = None` and `grant_patterns: list[list[str]] | None = None`
  (the latter is the already-existing "what to actually persist if it differs from the
  deterministic recomputation" escape hatch, kept as a separate value from `granted_preview`
  since the preview is a flattened display string while `grant_patterns` must stay structured).
* `action_confirm()`: `PermissionDecision(action=..., scope=..., grant_patterns=self._grant_patterns)`.

**`klorb/src/klorb/tui/mixins/interactions.py`** (`_confirm_permission_ask`)

* Replace the `ask_ctx.path is not None` / `ask_ctx.command is not None` / `ask_ctx.skill is not None` chain with an `isinstance(ask_ctx.resource, CommandResource)` check that, when a risk
  assessment offers a `suggested_pattern`, builds the displayed `GrantPreview` and the structured
  `grant_patterns` override from it directly; otherwise falls back to
  `ask_ctx.resource.grant_preview(self._session.config)` with `grant_patterns=None` (letting
  `apply_grant` recompute deterministically at persist time).

**`klorb/src/klorb/permissions/risk_classifier.py`**

* `_item_kind` dispatches on `isinstance(item.resource, CommandResource)`/`PathResource` instead
  of `item.command is not None`/`item.path is not None`.
* `_discard_unsafe_wildcard_argv0_patterns`/`_discard_nonmatching_suggested_patterns` guard on
  `isinstance(item.resource, CommandResource)` and read `item.resource.argv`.
* `record_decision_history`/`resolve_item_risk_assessment` gate on `ask_ctx.bash_context is None`
  instead of `ask_ctx.command_text is None`, and read `bash_context.item_command_text`/
  `command_text`/`intent` instead of the old top-level fields.
* `_sibling_items_for`'s fallback single-item synthesis switches to reading `ask_ctx.resource`/
  `ask_ctx.bash_context` instead of the individual fields — as a side effect this also fixes a
  small existing gap where the fallback silently dropped `ask_ctx.skill` when synthesizing,
  since it now copies `ask_ctx.resource` wholesale instead of enumerating fields one by one.

## Verification

* `make -C klorb lint typecheck`
* `make -C klorb test` (full suite — this refactor's blast radius means a real regression would
  likely show up as an existing test failure, not just the new ones)
* Update/add tests per the file list above; add `klorb/tests/klorb/permissions/test_resource.py`
  covering each `PermissionResource` subclass's `header_kind`/`preview_text`/`grant_preview`/
  `apply_grant`/`added_to_override`, plus a `StructuralResource` no-op-grant case and a
  `DomainResource` URL-vs-domain distinction case.
* No UI smoke test needed beyond the existing Textual `run_test()`-based panel tests — this is a
  library-and-TUI-layer refactor with no new runtime dependency or user-facing feature beyond the
  domain-ask preview fix, which the new tests cover directly.
