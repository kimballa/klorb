# Discover tools via a classmethod that returns a bootstrap registry; build session registries from a class dict

* Date: 2026-07-19 16:10
* Question: `ToolRegistry` discovered `Tool` subclasses by scanning a package inside
  `__init__`, so every registry — including each session-scoped one — paid the
  import/scan cost, and there was no way to build a registry from a *subset* of the
  discovered tools. With subagents coming that need a restricted tool set (see TODO.md's
  "ToolCatalog" item), how should `ToolRegistry` separate the one-time discovery from the
  per-session registry construction — introduce a separate `ToolCatalog` class, or keep a
  single `ToolRegistry` with two construction paths?
* Answer: Keep a single `ToolRegistry` class. `_discover_tools` is renamed to the
  `discover_tools(process_config, session_config, package=...)` `@classmethod`, which walks
  the package once and returns a `ToolRegistry` holding the discovered classes — the
  bootstrap registry of all tools the harness offers. `ToolRegistry.__init__` now takes a
  `dict[str, type[Tool]]` it clones into `self._tool_classes` (rather than a `package` to
  scan), so a session-scoped registry can be built from an already-discovered class dict —
  including a filtered subset of a bootstrap registry's classes — without re-scanning any
  package.
* Reasoning: A second `ToolCatalog` class would only thin-wrap `ToolRegistry` and add a
  name to learn; the only thing missing was a way to construct a registry from a known set
  of tool classes without re-running discovery. Putting discovery behind a classmethod that
  returns a registry, and making `__init__` dict-based, gives that with one type: the
  bootstrap call (`ToolRegistry.discover_tools(...)`) does the scan once, and every
  session/subagent registry is a plain `ToolRegistry(...)` built from a (possibly
  filtered) class dict. Cloning the dict (not holding it by reference) means a caller can
  freely mutate its own copy after handing it in — important once a subagent's restricted
  set is computed by filtering a shared bootstrap dict. The package scan never depended on
  config, so moving it out of `__init__` doesn't change what's discovered, only when;
  existing callers (the CLI, the TUI's `/clear`/restore paths, evals, tests) all reach the
  full tool set via `discover_tools` exactly as before. See
  [the original discovery ADR](discover-tools-by-scanning-the-tools-package.md) and
  [the fresh-instance-per-call ADR](tool-registry-instantiates-a-fresh-tool-per-call.md),
  which this refactors the *construction* of without changing the *factory* behavior those
  ADRs describe.
