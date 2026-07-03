# A homedir-scope grant may clean a stale workspace-file `ask` entry; a workspace-scope grant never touches the homedir/etc file

* Date: 2026-07-03 18:15
* Question: `readDirs`/`writeDirs` rules from every config layer (`/etc`, per-user, per-project,
  `--config`, last-session) are concatenated into one flat list with no rule-provenance tracking
  (see [the category-order ADR](evaluate-permission-categories-deny-then-ask-then-allow.md)), so
  a matched `ask` rule promoted during an interactive grant (see
  [the promotion ADR](promote-matched-ask-rule-path-not-candidate-on-grant.md)) might live in a
  *different* file than the one the current grant is being persisted to. Persisting a grant means
  editing exactly one file's own raw JSON (see docs/specs/permissions.md's "Interactive 'ask'
  confirmation"), so which file(s), if any, may be edited to remove a matched `ask` entry that
  doesn't live in the file the grant itself is being written to?
* Answer: A `"homedir"`-scope grant (persisted to `~/.config/klorb/klorb-config.json`) may
  *additionally* remove a matching, now-redundant `ask` entry from the **workspace** file
  (`${workspace_root}/.klorb/klorb-config.json`) if one independently exists there — removal
  only, never adding anything to that file's `allow`. The reverse is forbidden outright: a
  `"workspace"`-scope grant never opens the homedir file, or `/etc/klorb/klorb-config.json`, for
  any reason — not even to read it, let alone edit it. `klorb.permissions.grant
  ._clean_ask_entries_only()` implements the one permitted direction; nothing in the module ever
  calls it the other way.
* Reasoning: The permitted direction always flows from a *more*-trusted layer's explicit user
  decision toward cleaning up a *less*-trusted layer's leftover state, never the reverse. The
  workspace file is the least-trusted config layer in this system — it can arrive verbatim inside
  a cloned, untrusted repository, and a user running klorb from that `cwd` never explicitly
  reviewed its `readDirs`/`writeDirs` contents (see
  [the read/trust ADR](gate-read-hard-boundary-on-workspace-trust.md) for the same underlying
  trust asymmetry applied to reads). The homedir file, by contrast, is edited only in response to
  an explicit, interactive choice the user just made at the more powerful "for me" scope. Letting
  a `"homedir"` grant tidy up a workspace file's redundant `ask` entry is safe precisely because
  the direction of change is *only ever loosening* something the user's own higher-trust decision
  already superseded — it can never let an untrusted project layer reach up and silently mutate
  the user's personal config. Allowing the reverse would do exactly that: a workspace-scope grant
  (which a hostile repository's own bundled `.klorb/klorb-config.json` could itself be shaping,
  via a matching `ask` rule it planted) editing the user's homedir file on the user's own machine
  is the exact self-tampering risk this asymmetry exists to close off.

  In-memory removal from the live `SessionConfig` (and, for `"workspace"`/`"homedir"` scope, the
  `ProcessConfig.session` template) is unconditional and independent of this file-level
  asymmetry — it happens regardless of which file(s) actually get edited, so the running process
  is never left dominated by a stale `ask` entry it just granted an offsetting `allow` for. Only
  a *future* fresh process start, re-merging every layer from disk, can still see a stale `ask`
  entry this feature wasn't permitted to remove from its owning file — an accepted, inherent
  consequence of concatenation-based merging (see the category-order ADR), not something this
  asymmetry is meant to fully close.
