# Thread a `TrustManager` instance explicitly through every caller, not a module-level global

* Date: 2026-07-05 06:00
* Question: `projects.json` (the persistent registry of known project roots and whether each is
  trusted — see docs/specs/projects-and-trust.md) needs exactly one owner of its file I/O, so two
  concurrent writers (e.g. the initial workspace-bootstrap flow and a later "Trust workspace"
  command) can't race and clobber each other's update. PLAN-003 (the source plan for this
  feature) describes this owner as "a singleton `TrustManager` instance" — should
  `klorb.workspace.trust_manager` actually implement that as a module-level global (a
  `get_trust_manager()` accessor backed by a cached instance, the common meaning of "singleton"
  in Python), or as an ordinary class that happens to be constructed once per process and passed
  around explicitly?
* Answer: An ordinary class, constructed once by `klorb.cli.main()` and threaded explicitly to
  every collaborator that needs it (`klorb.process_config.load_process_config(workspace=...)`'s
  caller, `klorb.tui.repl.ReplApp.__init__(trust_manager=...)`) — no module-level global, no
  `get_trust_manager()` accessor, nothing cached at import time.
* Reasoning: Every other piece of shared, process-scoped state in this codebase already follows
  the explicit-construction-and-passing pattern — `ProcessConfig` is built once by
  `load_process_config()` and passed into `Session`/`ToolRegistry`/`ReplApp` constructors, never
  reached for via a module global; `Session`/`ToolRegistry` themselves are constructed once and
  handed to whatever needs them. A `TrustManager` global would be the one exception to that
  pattern, for no real benefit: "one owner of the file's I/O" only requires that *one instance*
  exists per process, which explicit construction in `klorb.cli.main()` already guarantees just
  as well as a cached global would, without introducing hidden shared mutable state that every
  test would otherwise need to reset between runs (a module-level cache surviving across tests
  unless explicitly monkeypatched/cleared is exactly the kind of test-isolation hazard
  `klorb.process_config`'s own test suite already goes out of its way to avoid — see
  `test_process_config.py`'s `_isolate_config_layers` fixture). It also keeps `TrustManager`
  trivially testable: every test in `test_trust_manager.py` constructs its own instance pointed
  at a `tmp_path`-scoped file, with no global state to patch or reset. `PLAN-003`'s "singleton"
  language is satisfied in the sense that matters (one real owner of the file per process
  lifetime), not in the sense of a Python singleton pattern.
