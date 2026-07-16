# Split ReplApp's methods into mixins, not composition objects

* Date: 2026-07-16 22:30
* Question: `klorb/src/klorb/tui/repl.py` grew to 3,456 lines, 63% of it one `ReplApp(App[None])`
  class with ~150 methods. Splitting the rest of the file (widget classes, constants, helper
  functions) into separate modules is a mechanical cut-and-paste; `ReplApp` itself can't be
  split the same way, since it's one class, not several standalone things. Composition (each
  concern becomes a helper object `ReplApp` holds a reference to, e.g.
  `self._prompt_controller = PromptController(self)`) or mixins (each concern becomes a plain
  class holding a slice of the methods unchanged, and `ReplApp` inherits from all of them)?
* Answer: Mixins. `ReplApp` is now
  `class ReplApp(KeyActionsMixin, WorkspaceBootstrapMixin, StatusBarMixin,
  PromptSubmissionMixin, RenderingMixin, InteractionsMixin, ReplAppBase)`, with each mixin in
  its own file under `klorb/tui/mixins/` holding one cohesive slice of the original class's
  methods, moved verbatim. `ReplAppBase` (`klorb/tui/_base.py`) is an attribute-and-method-stub
  declaration class every mixin inherits from, so each mixin file type-checks standalone.
* Reasoning: Composition would have required rewriting every call site referencing a moved
  method or attribute (`self._mount_tool_call_widget(...)` becoming
  `self._rendering.mount_tool_call_widget(...)`, or `self._tool_call_widgets` becoming
  `self._rendering.tool_call_widgets`, at every one of dozens of call sites scattered across
  the original class) -- and the explicit goal of this split was that the *move itself* be
  mechanical line-range extraction (`sed`-based cut/paste), not an agent retyping ~8,300 lines
  of Textual/asyncio code from its own understanding, which is exactly how a dropped `await`,
  a silently-renamed local, or a reordered CSS rule sneaks in unnoticed. Mixins let every method
  body move unchanged: `self.foo(...)` resolves identically at runtime via MRO regardless of
  which mixin `foo` physically lives in, so zero call sites needed editing.

  The cost is typing, not runtime behavior: this repo's `make typecheck` runs mypy with
  `--disallow-untyped-calls`/`--disallow-untyped-globals`/`--disallow-redefinition`, so a mixin
  method referencing `self._some_attr` (set by `ReplApp.__init__`, which lives in a different
  file, `app.py`) needs `_some_attr` visible on `self`'s declared type from that mixin's own
  point of view -- mypy has no way to know that whichever concrete class eventually mixes this
  one in will also mix in the one that sets it. `ReplAppBase` solves this once, centrally:
  every attribute `__init__` sets and every method one mixin calls on `self` but doesn't itself
  define gets a declaration (or, for a non-`None` return type mypy's `--disallow-untyped-calls`
  won't accept a bare `...` body for, a `raise NotImplementedError` stub) on `ReplAppBase`, and
  every mixin declares `class FooMixin(ReplAppBase):` instead of subclassing `App[None]`
  directly. `app.py`'s `ReplApp` is the only class with a real `__init__` body.

  This pattern is new to this codebase -- no other module mixes in multiple non-cooperative
  base classes this way -- but the alternative (composition) would have defeated the actual
  point of the exercise, which was a safe, verifiable-by-diff move of a very large class, not a
  redesign of it. If `ReplApp`'s responsibilities are ever redesigned from scratch, composition
  remains available then; this split deliberately doesn't foreclose it, since each mixin's
  methods are still a cohesive, nameable unit that composition could adopt wholesale.
