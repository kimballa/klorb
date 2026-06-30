# Discover tools by scanning the tools package, not a manual registry list

* Date: 2026-06-29 23:00
* Question: How does `ToolRegistry` learn which `Tool` implementations exist — an explicit
  list maintained somewhere, or automatic discovery?
* Answer: `ToolRegistry` walks the modules of a package (the `klorb.tools` package by
  default) with `pkgutil.iter_modules`, imports each module, and collects any concrete
  `Tool` subclasses defined directly in it.
* Reasoning: With automatic discovery, adding a new tool is just "drop a module in
  `klorb/src/klorb/tools/` defining a `Tool` subclass" — there's no separate list to edit
  and keep in sync, so it can't drift out of date or be forgotten. The package is passed
  into the constructor rather than hardcoded, so tests can point discovery at a fixture
  package instead of the real one, and so callers aren't forced to use a single global
  tools namespace if that's ever needed.
