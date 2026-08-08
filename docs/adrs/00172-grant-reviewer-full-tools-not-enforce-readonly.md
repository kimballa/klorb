# Grant the Reviewer role full tools, not `enforce_readonly_tools`

* Date: 2026-08-06 00:00
* Question: The `reviewer` subagent role (docs/specs/subagents.md's "Reviewer role") audits a
  completed change and needs to confirm suspected bugs, not just theorize about them from a
  diff. Should its `agents.json` entry follow Explorer's pattern -- `enforce_readonly_tools:
  true`, a narrow named `tools` list -- or inherit its creator's full tool set unrestricted?
* Answer: Full inheritance. `reviewer`'s `restrict_to` sets no `tools`/`skills`/
  `tool_categories` and leaves `enforce_readonly_tools` at its `false` default, so a Reviewer
  gets exactly what its creator (today, always `operator`) has -- including `Bash` and the
  file-edit tools.
* Reasoning: [[debug-with-evidence]]'s whole premise is that reasoning about unobserved
  behavior is guessing with extra steps -- running something is what actually narrows a
  hypothesis down. A reviewer confirming "is this really a bug" needs the same capability a
  developer debugging it would: run the failing path, write a throwaway reproduction script, or
  make a temporary probe edit and observe. `enforce_readonly_tools` would force every
  confirmation into pure reading, which produces reports hedged with "this looks like it might
  be a bug" instead of "confirmed: passing `-1` to `foo()` raises `IndexError` at line 42" --
  exactly the noisy, unconvincing findings [[debug-with-evidence]] and the `code-review` skill
  both warn against manufacturing. The discipline that keeps a Reviewer from actually *fixing*
  what it finds is therefore carried in the role's own prompt (`roles/reviewer/default.md`:
  "any temporary change ... must be unwound before you finish") and the `code-review` skill's
  step 4, not in a tool-level restriction -- the same tools that let it confirm a bug would let
  it patch one, so nothing at the tool layer can distinguish "probing" from "fixing"; only
  instructed discipline can. This is a narrower trust boundary than Explorer's (a Reviewer
  *could* misbehave and modify the codebase, where an Explorer structurally cannot), accepted
  because a Reviewer is launched by, and reports back to, the same operator session that already
  has that access itself -- unlike Explorer, granting a Reviewer nothing it doesn't already
  inherit from its own creator, per docs/specs/subagents.md's "Security model" invariant.
  Alternatives rejected: a bespoke `tools` allowlist mirroring Operator's own set (duplicates a
  list that would drift out of sync every time a new tool is added, for no behavioral gain over
  plain inheritance); `enforce_readonly_tools: true` with an escape hatch for `Bash` specifically
  (still lets it call `EditFile`/`CreateFile` through the shell, so the restriction would be
  theater without actually narrowing anything).
