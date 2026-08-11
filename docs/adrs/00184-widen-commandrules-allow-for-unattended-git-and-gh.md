# 2026-08-10: Widen `commandRules.allow` for unattended git and gh commands

## Question

Software factory mode (`docs/specs/software-factory.md`) runs a klorb session against this repo
with no human present to answer a permission prompt. It needs to branch, commit, push, and open
PRs on its own. This repo's `.klorb/klorb-config.json` `commandRules.allow` list was, until now,
read-only (`status`/`diff`/`log`/`show`/`stash`/`ls-files`) — `git checkout`, `git commit`,
`git push`, and `gh pr create` all would have landed on `ask`, which nothing is present to
answer. How should the loop get permission to run these unattended?

## Answer

Add `git checkout`, `git branch`, `git add`, `git commit`, `git push`, `git rm`, `git mv`, and
`gh pr create` to `commandRules.allow` in this repo's own `.klorb/klorb-config.json`. This is a
workspace-wide grant — it applies to any klorb session run against this workspace, not only a
software-factory run.

## Reasoning

klorb has no mechanism today to scope a command grant to "only while a specific skill is
active" — `commandRules` and `skillRules` are independent fields, and `EscalatePrivileges` (the
one existing scoped-grant mechanism) only covers filesystem access under `.klorb`/config
directories, not `CommandPermissionsTable`. Building skill-scoped command grants is a real
platform feature but out of scope for this change; it's tracked separately in `TODO.md` as a
future idea.

Given that, the only way to let this loop run unattended today is a workspace-level grant, and
`.klorb/klorb-config.json` for this specific repo is exactly the place such a deliberate,
narrowly-scoped trust decision belongs — it doesn't ship to klorb's other users, and it's a
repo the author already trusts an agent to modify autonomously. The alternative (leaving these
on `ask`) would make unattended operation impossible, defeating the point of the feature.
