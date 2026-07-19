---
name: encapsulate-in-classes
description: Decide whether new Python state/behavior needs a class while writing or reviewing klorb source. Use whenever new code introduces module-level mutable state (a variable reassigned by one or more functions via a `global` statement), a "there's only one of these" singleton, a cluster of free functions that all read/write the same implicit state, or a function returning a positional tuple of more than one loosely-related value (dicts, primitives, other tuples). Also use when reviewing a diff that adds any of these shapes.
---

# Encapsulating state in classes

`AGENTS.md` already states the baseline rule: encapsulate related state and behavior in a class,
even when there's only one instance — avoid module-level mutable globals paired with free
functions that read/write them, and avoid returning a bare tuple of loosely-related values from a
function. This skill is the operational checklist for applying that rule: which code shapes need a
class, and exactly how to shape one.

The guiding question is always: **if I described this code's design to someone without showing
them the implementation, would they say "that's an object" or "that's just some functions and some
variables that happen to agree with each other"?** Free functions mutating a module global via
`global` are the second thing wearing the first thing's job.

## 1. Singleton state: wrap it in a class, even though there's only ever one instance

"There's only one of these in the whole process" is a reason to make access to it simple (one
accessor function), not a reason to skip the class. A module carrying `_thing: X | None = None`
plus three or four functions that each open with `global _thing` is exactly the anti-pattern:
the state and the functions that mutate it are logically one object, they're just not spelled that
way.

**Before** (the shape to avoid — this is what `klorb.tools.skill.catalog` originally looked like):

```python
_typed_catalog: SkillCatalog | None = None
_canonical_catalog: SkillCatalog | None = None

def ensure_skill_catalog(*, workspace_root, workspace_trusted, claude_skills_compat) -> None:
    if _typed_catalog is not None:
        return
    reload_skill_catalog(...)

def reload_skill_catalog(*, workspace_root, workspace_trusted, claude_skills_compat):
    global _typed_catalog, _canonical_catalog
    typed, canonical = build_catalogs(...)
    _typed_catalog, _canonical_catalog = typed, canonical
    return typed, canonical

def canonical_catalog() -> SkillCatalog:
    if _canonical_catalog is None:
        raise RuntimeError("not built yet")
    return _canonical_catalog
```

**After** (the actual current shape of `klorb.tools.skill.catalog`):

```python
class SkillCatalogRegistry:
    """Holds the two process-wide skill catalogs and the logic to (re)build them. Exactly one
    instance exists per process; nothing outside this class reads or writes its state directly."""

    def __init__(self) -> None:
        self._typed: SkillCatalog | None = None
        self._canonical: SkillCatalog | None = None

    def ensure(self, *, workspace_root, workspace_trusted, claude_skills_compat) -> None:
        if self._typed is not None:
            return
        self.reload(...)

    def reload(self, *, workspace_root, workspace_trusted, claude_skills_compat) -> SkillCatalogs:
        catalogs = build_catalogs(...)
        self._typed, self._canonical = catalogs.typed, catalogs.canonical
        return catalogs

    def canonical(self) -> SkillCatalog:
        if self._canonical is None:
            raise RuntimeError("not built yet")
        return self._canonical


_registry = SkillCatalogRegistry()


def get_skill_catalog_registry() -> SkillCatalogRegistry:
    """Return the process-wide `SkillCatalogRegistry` singleton."""
    return _registry
```

Every caller reaches the singleton through `get_skill_catalog_registry()` and calls a method on it
(`registry.ensure(...)`, `registry.canonical()`) — nothing outside `catalog.py` ever touches
`_registry` or `_typed`/`_canonical` directly. The private (`_`-prefixed) attributes are the whole
point: they make it structurally impossible for another module to reach in and mutate state that
belongs to the registry, the same protection a `global` statement never provided in the first
place (any module could always just import the bare name and reassign it).

`klorb.models.registry.ModelRegistry` and `klorb.tools.registry.ToolRegistry` are the existing
precedent for this shape in klorb, though neither of those happens to be a process-wide singleton
— they're constructed explicitly (`ModelRegistry()`, `ToolRegistry(process_config, session_config)`)
and passed around, which is an even stronger version of the same idea (no global reference to reach
at all, not even through a single accessor). Prefer that when the object's lifetime naturally
belongs to something else already being constructed and threaded through (a `Session`, a
`ToolSetupContext`); reach for a singleton-with-accessor only when the state genuinely needs to
survive independent of any one such owner (see docs/adrs/
build-the-skill-catalog-once-per-process-not-per-call.md for why the skill catalog specifically
needs to outlive any one `Session`).

## 2. A function returning more than one loosely-related value: give it a class, not a tuple

`def f() -> tuple[str, int]` forces every caller to either unpack positionally (`a, b = f()` —
easy to get the order wrong with no type error) or index (`f()[0]` — meaningless without reading
the function). If the two values are conceptually one thing — a result, a bundle, a pair that's
always produced and consumed together — say so with a class.

**Before:**

```python
def build_catalogs(...) -> tuple[SkillCatalog, SkillCatalog]:
    ...
    return typed_dict_wrapped, canonical_dict_wrapped

typed, canonical = build_catalogs(...)  # which one is which, again?
```

**After:**

```python
class SkillCatalogs(BaseModel):
    """The (typed, canonical) catalog pair `build_catalogs()` produces together from one disk
    scan -- a small named bundle rather than a positional tuple, since the two are always built
    and consumed as a pair."""
    model_config = ConfigDict(frozen=True)
    typed: SkillCatalog
    canonical: SkillCatalog

def build_catalogs(...) -> SkillCatalogs:
    ...
    return SkillCatalogs(typed=..., canonical=...)

catalogs = build_catalogs(...)
catalogs.canonical  # self-documenting at the call site
```

The same applies to two independently-`None`-able return values that are only ever meaningful
*together*. `klorb.session.Session._build_user_skill_activation_interjection` used to return
`tuple[str | None, tuple[str, str] | None]` — the caller had to `assert` that the second value
was set whenever the first was, because the type system couldn't express "both or neither." A
`UserSkillActivation` pydantic model with non-optional `body`/`skill_id` fields, and the whole
function returning `UserSkillActivation | None`, makes the "both or neither" invariant a type-level
fact instead of a runtime assumption.

## 3. What this rule does *not* mean: pure, stateless functions are fine

Don't turn every function into a class. A function that takes explicit parameters, touches no
shared/global state, and returns a plain value is ordinary, encouraged functional style — nothing
here asks you to wrap `klorb.tools.skill.common.parse_frontmatter(text: str) -> dict[str, Any]` or
`klorb.permissions.skill_access.format_fqsn(skill_id: SkillId) -> str` in a class. The tell is
whether the function depends on or mutates state that isn't in its own parameters/return value —
if every call with the same arguments gives the same result and changes nothing outside its own
return value, it's a fine free function. The anti-pattern is specifically *implicit shared state*
(a module global, an ambient singleton) and *ambiguous multi-value returns*, not "using a function
instead of a class" in general.

## Checklist when writing or reviewing a change

- [ ] Any new module-level variable reassigned inside a function via `global`: could this instead
      be a private instance attribute on a class, with that function becoming a method?
- [ ] Any new "there's only one of these" object: does it have a class with private state and
      public methods, reached through exactly one accessor function (or, better, constructed once
      and threaded through explicitly, like `ModelRegistry`/`ToolRegistry`) — rather than several
      free functions each doing `global` and a naked module variable?
- [ ] Any new function returning `tuple[X, Y, ...]`: are `X`/`Y`/... independent values a caller
      might reasonably want separately (a plain tuple is fine), or are they always produced and
      consumed together (give it a named class instead)?
- [ ] Any new function returning two or more independently-`None`-able values meant to be
      "both set or neither": can the type system express that invariant directly (a single
      `SomeResult | None` return, where `SomeResult`'s fields are non-optional) instead of a tuple
      of parallel optionals plus a runtime `assert`?
- [ ] Is a genuinely pure, stateless helper function being left alone rather than needlessly
      wrapped in a class just because "more classes" felt like the safe default?
