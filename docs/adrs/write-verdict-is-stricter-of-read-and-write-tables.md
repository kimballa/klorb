# Derive a write verdict from both `readDirs` and `writeDirs`, taking the stricter, with an unmatched table normalized to `ask`

* Date: 2026-07-02 15:40
* Question: `resolve_and_evaluate_read()` previously fell through to the `writeDirs` table
  whenever `readDirs` matched nothing at all (the "six-step chain": `readDirs.deny -> ... ->
  readDirs.allow -> writeDirs.deny -> ... -> writeDirs.allow`), while `evaluate_write()`
  consulted `writeDirs` alone, falling back to `"allow"` when nothing matched. This let a path be
  writable but not readable — e.g. `readDirs.deny=["/foo"]` plus `writeDirs.allow=["/foo"]`
  produces a "write-only" directory, which is backwards: read should never be *more* restrictive
  than write for the same path. Should write's evaluation change to restore that invariant, and
  if so, what should it do when one table has an opinion on a path and the other doesn't?
* Answer: `readDirs` and `writeDirs` are now evaluated independently, each against its own table
  only — `resolve_and_evaluate_read()` no longer touches `writeDirs` at all; it keeps its
  existing per-trust no-match fallback (`"allow"` inside the workspace when untrusted, `"deny"`
  when trusted). `evaluate_write()` computes both tables' raw verdicts for the path
  (`deny`/`ask`/`allow`, or `None` if neither table's three lists match at all), normalizes each
  via `_normalize_for_write()` — which maps `None` to `"ask"`, not to a permissive default — and
  returns the *stricter* of the two normalized verdicts (`deny` > `ask` > `allow`). Concretely:
  write is `"allow"` only when both tables say `allow`; write is `"deny"` if either table says
  `deny`; a path `writeDirs` never mentions is `"ask"` for write even if `readDirs` explicitly
  allows it, and a path `readDirs` never mentions is likewise `"ask"` for write even if
  `writeDirs` explicitly allows it. The full 4x4 (`readDirs` verdict x `writeDirs` verdict,
  each including `None`) matrix is captured directly as
  `test_write_merge_matrix` in `klorb/tests/test_permissions.py`.
* Reasoning: This restores "write is never more permissive than read" as a structural invariant
  rather than a convention config authors have to maintain by hand (previously: to fully block
  both read and write to a directory that a broader `readDirs.allow` already covered, you'd need
  matching entries in *both* deny lists — easy to get only half right). Normalizing an unmatched
  table to `"ask"` (rather than having `evaluate_write()` defer entirely to whatever `readDirs`
  says when `writeDirs` is silent) was a deliberate choice over the more permissive alternative:
  deferring to read would mean a bare `readDirs.allow` — with no `writeDirs` entry at all — makes
  a path writable, silently reintroducing a different version of the same problem (a read grant
  implicitly becoming a write grant) and requiring careful, easy-to-miss `writeDirs.deny` entries
  to claw back read-only directories. Requiring an explicit `allow` in *both* tables makes write
  access opt-in and symmetric to configure: granting write to a new directory means adding it to
  both `readDirs.allow` and `writeDirs.allow`, not just one.

  This is an accepted regression to the write tools' previous zero-config default: with both
  `readDirs`/`writeDirs` empty, write used to fall back to `"allow"` everywhere in the workspace;
  it now normalizes to `"ask"` everywhere, which — since `PermissionAskRequired` still fails
  closed with no interactive prompting plumbing yet (see
  [the category-order ADR](evaluate-permission-categories-deny-then-ask-then-allow.md)) — means
  write is effectively denied everywhere until either config explicitly grants it or the
  interactive "ask" flow (`TODO.md`'s "Permissions" item) exists. `readDirs`'s own no-match
  fallback is untouched, so read-only zero-config workflows are unaffected. This tradeoff was
  made deliberately in favor of correctness over the old default's convenience, since the write
  permission system is new enough that no shipped workflow depends on the old implicit-allow
  default, and a directory that's silently writable by default is a worse failure mode than one
  that asks and fails closed.
