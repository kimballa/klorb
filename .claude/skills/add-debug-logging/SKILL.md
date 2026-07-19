---
name: add-debug-logging
description: Decide where to add logger.debug()/logger.info() breadcrumbs while writing or reviewing klorb source. Use whenever new code performs a system-level action (filesystem create/delete/move, subprocess spawn, permission escalation), reconciles auth/permission/allow-deny logic, does package/module discovery or other recursive tree crawling, or loops over many items (files, dirs, entries) doing something consequential to each. Also use when a change silently skips or prunes something (a filtered directory, a denied path) that would otherwise be invisible for troubleshooting.
---

# Adding debug-log breadcrumbs

`AGENTS.md` already states the baseline rule: log consequential actions and workflows —
creating/removing files or directories, registering cleanup handlers, granting or widening
permissions, spawning subprocesses or sessions — at `debug` level, erring on the side of logging
*more* of these than feels necessary. This skill is the operational checklist for applying that
rule: which code shapes need a breadcrumb, and exactly where in the code the breadcrumb goes.

The guiding question is always: **if this went wrong in the field, hidden from the user and the
agent, could the debug log alone tell us what actually happened?** A silent skip, a silent prune,
a loop that touched 40 files but only proves it touched *one* — none of those are reconstructable
after the fact without a line at the point of decision.

## 1. System-level actions always get a debug line

Anything that mutates state outside the current Python process — the filesystem, a subprocess, a
permission grant, a registered `atexit` handler — gets a `logger.debug()` right at (or just
before) the point where it happens, not summarized after the fact. Compare two real examples:

* `klorb/src/klorb/permissions/directory_access.py`'s `create_tempdir_for_session()` logs the
  tempdir path right after creating it, and again if it registers an `atexit` cleanup for it —
  two separate consequential facts, two separate lines.
* `klorb/src/klorb/tools/util/edit_file_core.py`'s `EditFileCore` logs immediately before
  `path.parent.mkdir(parents=True, exist_ok=True)`, naming the path being created.
* `klorb/src/klorb/tools/memory/edit_memory.py`'s `EditMemoryTool` logs immediately before
  `path.unlink(missing_ok=True)` when it has to undo an auto-created file, explaining *why*
  (`"undoing auto-create: first line would be blank"`) — not just that a delete happened.

The "why," not just the "what," matters: a bare `logger.debug("deleting %s", path)` tells you a
delete happened; it doesn't tell you whether that was the intended cleanup path or a bug.

## 2. Auth/permission/allow-deny reconciliation logs its own reasoning

Anywhere code reconciles a rule set — an allow/deny/ask table, a risk classifier's suggested
pattern, a scope check — log what was decided and why, not just the final boolean. See
`klorb/src/klorb/permissions/risk_classifier.py`: when a suggested Bash pattern is discarded
for wildcarding `argv0` unsafely, or for not matching the command it was generated for, each case
gets its own `logger.info()` naming the specific pattern and the specific reason it was rejected,
before falling back to a literal-argv grant. A troubleshooter reading the log later can tell
*which* safeguard fired, not just that "something" fell back to the safe default.

This matters even more when the surrounding tool contract is "fail closed silently to the caller"
(deny without a raised warning, prune without a flag) — see `docs/adrs/prune-non-allow-subdirs-*`
and the `.git`-skip case below. The agent-facing behavior is deliberately quiet; the debug log is
where that quiet decision still has to be visible to whoever's debugging it.

## 3. Package/module discovery and recursive crawls: log the pass, and log each item

A discovery or tree-walk loop gets **two** debug touchpoints, not one:

* One line before the loop starts, naming what's being discovered/walked (the intent).
* One line *inside* the loop, per item found/kept/skipped — not a single summary after the loop
  ends. The per-item line is what makes an unexpected member of the result (or an unexpected
  absence) traceable to *which* iteration produced it.

`klorb/src/klorb/tools/registry.py`'s `_discover_tools()` is the canonical shape:

```python
logger.debug("Discovering tools in package %s", package.__name__)
...
for module_info in pkgutil.walk_packages(package.__path__, prefix):
    ...
    logger.debug("Registered tool %r from %s", tool.name(), module.__name__)
    ...
logger.info("Discovered %d tool(s) in %s", len(self._tool_classes), package.__name__)
```

A recursive directory walk that silently prunes something follows the same shape: the prune
decision is logged **at the point in the recursion where the prune happens**, not hoisted above
the loop as a one-time notice and not left mute because the walk's *contract* is "skip silently."
`klorb/src/klorb/tools/util/dir_walk.py`'s hard-coded `.git`-directory skip is a worked example —
the walk deliberately tells neither the agent nor the caller's return value that anything was
skipped (see `docs/adrs/hard-code-skip-dot-git-dirs-in-tree-walk.md`), which is exactly why the
debug line at the `continue` is load-bearing:

```python
if entry.name == GIT_DIR_NAME:
    logger.debug("walk_readable_tree skipping .git dir %s", entry)
    continue
```

## 4. A loop over many files/entries logs inside the loop, per entry — never just once ahead of it

If code is about to create, delete, move, or otherwise act on a *collection* of files or
directories, the debug line goes **inside** the loop body, so every single item is individually
accounted for in the log — not a single line before the loop announcing the batch. A batch-level
line undercounts: if the loop dies partway through (exception, cancellation), the log can't tell
you which items were actually processed and which weren't.

`klorb/src/klorb/permissions/directory_access.py`'s `_remove_registered_tempdirs()` (the `atexit`
handler that best-effort `rmtree`s every registered scratch tempdir) is the shape to copy:

```python
for directory in _tempdirs_to_remove_on_exit:
    logger.debug("atexit: removing registered scratch tempdir %s", directory)
    shutil.rmtree(directory, ignore_errors=True)
```

Even though every iteration does "the same thing," each one gets its own line — that's what lets
a partial-failure investigation say "these three were removed, the fourth was not" instead of
"the cleanup function ran."

## 5. Bounded loops and retry/round logic: log every round, and log why the loop ends

A loop with a round counter or a retry budget (tool-call rounds, request retries) logs each round
as it happens, and logs distinctly depending on how the loop terminates — success, a retry, or
hitting the limit. See `klorb/src/klorb/session.py`'s tool-call round loop: `logger.info()` fires
once per round with the round number and the cap (`"Turn tool-call round %d/%d for %s"`), a
separate `logger.warning()` fires only if the cap is actually hit before raising
`ToolCallLimitExceeded`, and the enclosing turn logs a final `logger.debug()`/`logger.info()`
either way (`"Turn complete for %s: ..."`, `"Turn aborted for %s"`, or `logger.error()` with
`exc_info=True` on an unhandled exception). Three different exits, three different messages —
never collapse them into one generic "loop finished" line.

## 6. `debug` vs. `info`/`warning`: keep the audience distinction

This skill is about `debug`-level breadcrumbs specifically — internal, always-on-when-enabled
diagnostic trail, not meant for a user to see. `logger.info()`/`logger.warning()` stay reserved
for what a user actually needs surfaced (a retry happening, a request that took unusually long, a
fallback being applied) — see the risk-classifier and session examples above, which mix both
levels deliberately. When deciding the level for a new line, ask who the audience is: "someone
debugging a report of unexpected behavior" is `debug`; "the person currently running klorb" is
`info`/`warning`. Don't default new lines to `info` just because `debug` output is off by default
in most runs — that's the entire point of the level existing.

## Checklist when writing or reviewing a change

- [ ] Any new filesystem/subprocess/permission mutation: is there a `logger.debug()` naming the
      exact path/command/scope, placed at (not far above) the point where it happens?
- [ ] Any new allow/deny/ask/risk decision: does the log line say *which* rule fired and *why*,
      not just the resulting verdict?
- [ ] Any new discovery or recursive walk: is there an intent line before the loop, and a
      per-item line inside it?
- [ ] Any new loop touching multiple files/dirs: is the log line inside the loop body, one per
      item, rather than a single line before or after the loop?
- [ ] Any new bounded/retry loop: does each exit path (success, retry, limit-hit, exception) get
      its own distinct message?
- [ ] Is the level right for the audience — `debug` for "reconstruct what happened," `info`/
      `warning` for "the user should see this"?
