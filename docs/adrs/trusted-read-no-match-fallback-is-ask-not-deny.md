# Trusted-workspace `ReadFile` no-match fallback is `"ask"`, not `"deny"`

* Date: 2026-07-08 16:00
* Question: `resolve_and_evaluate_read()`'s trusted-workspace branch (see
  docs/adrs/gate-read-hard-boundary-on-workspace-trust.md) evaluates a candidate path against
  `readDirs` with no hard boundary, then falls back to a fixed verdict when nothing in `readDirs`
  (deny/ask/allow) mentions the path at all. Should that fallback be `"deny"` (refuse outright,
  never reaching the interactive-ask flow) or `"ask"` (the same no-match fallback
  `evaluate_write()` already uses)?
* Answer: `"ask"`. A path `readDirs` has no opinion on in a trusted workspace now reaches
  `PermissionAskRequired`, the same interactive-confirmation path `evaluate_write()`'s own
  no-match fallback already uses, instead of `raise_if_not_allowed()` turning it into an
  unconditional `PermissionError` before the user is ever consulted.
* Reasoning: `"deny"` as the fallback meant a trusted project could still never prompt about a
  path outside whatever `readDirs` already happened to mention — e.g. a file under `/tmp` a user
  explicitly asked the agent to read. The only way to make such a path reachable at all was to
  hand-edit `readDirs` in `klorb-config.json` first; the interactive-ask machinery this whole
  permission system exists to drive was structurally unreachable for any read path that wasn't
  already pre-listed, in the one mode (`workspace.trusted`) where reaching outside
  `workspace_root` is the entire point.

  This was also an unintended asymmetry with `evaluate_write()`, whose own no-match fallback is
  `"ask"` (see `_normalize_for_write()`), not `"deny"` — so before this change, an unmentioned
  path was *more* forgiving for writes than for reads inside a trusted project, backwards from
  docs/adrs/gate-read-hard-boundary-on-workspace-trust.md's own stated premise that read is the
  lower-blast-radius operation of the two and should therefore be the more permissive one, not
  the more restrictive one.

  This doesn't reopen the risk that motivated the original hard-boundary ADR: a headless session
  still fails closed on `"ask"` exactly as it does today (see
  docs/adrs/default-permission-framework-to-deny-headlessly.md), and a `readDirs.deny` entry —
  from any config layer, including the user's own trusted `/etc`/homedir layers — still outranks
  this fallback entirely, since the table is consulted before the fallback is ever reached. The
  only behavior this changes is that an interactive session, in a workspace the user already
  chose to trust, now gets asked about a path nobody has an opinion on yet, instead of being
  refused with no recourse but editing a config file by hand.
