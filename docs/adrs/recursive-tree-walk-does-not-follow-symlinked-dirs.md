# Recursive tree walk never descends into a symlinked subdirectory

* Date: 2026-07-04 01:19
* Question: `klorb.tools.util.walk_readable_tree()` (shared by `GrepTool` and
  `FindFileTool`) recurses depth-first through however many directory levels a tree has. Nothing
  about the permission checks alone (`readDirs` containment, the workspace-root boundary) stops a
  symlinked subdirectory from pointing back at one of its own ancestors, which would make an
  unbounded walk recurse forever. Should the walk follow symlinked subdirectories (descending
  into them, subject to the same `readDirs`/workspace-boundary checks as any other subdirectory),
  or skip them entirely?
* Answer: Skip them entirely — a subdirectory that `Path.is_symlink()` reports true for is
  excluded from `subdir_names` and never descended into, unconditionally, regardless of what its
  own `resolve_and_evaluate_read()` verdict would have been. This mirrors Python's own
  `os.walk(..., followlinks=False)` default. The root passed to `walk_readable_tree()` itself is
  still resolved through `resolve_and_evaluate_read()` exactly like `ListDir`'s `dirname` — which
  does follow a symlink if `dirname` itself names one — so this restriction is specific to
  subdirectories discovered *during* the walk, not the explicit target the caller named.
* Reasoning: A symlink cycle (`a/b -> a`, or subtler multi-hop cycles) would otherwise make
  `walk_readable_tree()` recurse without bound, since nothing else in the walk tracks visited
  directories to detect one. Refusing to follow *any* symlinked subdirectory sidesteps that
  entirely, at the cost of also refusing to follow a symlink that doesn't happen to cycle back —
  the same trade-off `os.walk` itself makes by defaulting `followlinks` to `False`. Detecting
  cycles precisely (e.g. tracking each directory's real device/inode pair and refusing only a
  repeat) is more code for a case (deliberately symlinking a subdirectory into a project tree the
  agent might legitimately want searched) that hasn't come up as a real need; if it does, it can
  be revisited without changing this ADR's answer for the common case, since the fallback of
  naming the symlink's target directly as `dirname` in a fresh `Grep`/`FindFile` call already
  works today (the root-level check follows symlinks, as noted above).
