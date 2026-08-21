---
name: enable-software-factory
description: >
  Autonomously pull small tasks from docs/plans/auto/, build each one on its own branch, get
  it reviewed, open a PR, clean up the task queue, and stop -- the onAgentTurnEnd hook restarts
  this skill for the next task once the working copy is clean again. Use when asked to "enable
  software factory", "run the software factory", "find the next auto task", or similar.
metadata:
  klorb:
    bashCommands:
      - ["git", "checkout", "**"]
      - ["git", "branch", "**"]
      - ["git", "push", "**"]
      - ["git", "rm", "**"]
      - ["touch", "docs/plans/auto/.factory_in_progress.tmp"]
      - ["gh", "auth", "status"]
      - ["gh", "pr", "**"]
---

# Enable software factory

This skill drives one iteration of an unattended build loop: pick a task from `docs/plans/auto/`,
implement it, get it reviewed, land it, and clean up the task queue — then stop. Software-factory
mode (a sentinel file under `docs/plans/auto/`, created by this skill's own activation hook) is
what causes each iteration to restart the next one automatically; this skill doesn't loop itself.

## 1. Check the working copy is clean

Run `git status --porcelain`. If it's not empty, **stop here** and report what's dirty. Do not
stash, discard, or otherwise clean it up yourself — that's uncommitted human work, and it's not
safe to assume it's safe to build on top of or throw away.

## 2. Start from `main`

`git checkout main`. Always start looking for work from `main`, never from whatever branch a
previous iteration happened to leave checked out.

## 3. Find the next task

Look in `docs/plans/auto/`:

1. `docs/plans/auto/queue.md` first. A task is a top-level (unindented) bullet — a line starting
   with `- ` or `* `. Headings, prose, blank lines, and HTML comments are not tasks; they're free
   to use for notes about the format. Take the first task line.
2. If `queue.md` has no task lines, look for any other `.md`/`.txt` file directly under
   `docs/plans/auto/` (not `queue.md` itself). Each one is a single, self-contained, whole-file
   task — read the whole file and treat it as one unit of work.
3. If neither has anything, report that there's no work and stop. Don't invent a task.

## 4. Mark work in progress

`touch docs/plans/auto/.factory_in_progress.tmp`. This file is gitignored — it exists purely so
the `onAgentTurnEnd` hook can tell "this task is mid-flight, nudge me to continue" apart from "the
tree is dirty for some unrelated reason, leave it alone."

## 5. Create a feature branch

Branch from `main`: `git checkout -b feat/<slug>` (or `fix/<slug>` for a bug task). Keep the slug
short and descriptive, under ~50 characters.

## 6. Implement

Follow `AGENTS.md`'s standing conventions:

- Read existing code before modifying it. Make the smallest change that correctly accomplishes
  the task.
- Add `logger.debug()` breadcrumbs at consequential moments (file/dir creation or removal,
  subprocess spawns, permission grants).
- Write or update tests — the task isn't done until they pass.
- Run `make lint`, `make typecheck`, and `make test` from the appropriate subdirectory (`klorb/`
  for harness work, `vscode-plugin/` for plugin work).
- If the task touches a spec under `docs/specs/`, update the spec to match.
- Make intermediate commits as logical units of work land.

## 7. Code review

Once implementation is complete and tests pass, launch a **Reviewer** subagent for an independent
review. Activate the `code-review` skill for review mechanics.

```python
CreateSubagent(
    role="reviewer",
    session_title="Review: <short task description>",
    initial_message=(
        "Review the changes on this branch compared to main. "
        "The task was: <paste the task's full text>. "
        "Run `git diff main..HEAD` to see the full diff. "
        "Check for logic bugs, architectural fit, security, test coverage, "
        "and conformance to project conventions in AGENTS.md. "
        "Report findings prioritized by severity."
    )
)
```

Wait for the reviewer's report (`WaitForSubagent`), even if it takes several tries due to
timeouts — code review takes minutes, not seconds. Do not substitute your own self-review for it.

For each finding: decide if it's real (the reviewer is a lead, not an oracle), fix real issues,
re-run tests after each fix. If you made substantive changes, launch the reviewer again. Loop
until it reports no blocking findings, capped at 3 rounds — after that, use your own judgment on
what's left.

## 8. Finish on the feature branch

Remove the task you just built from its source: delete the consumed bullet from `queue.md`, or
`git rm` the whole-file task. Commit that removal together with (or immediately after) the
implementation commits. Then:

```bash
git push -u origin feat/<slug>
gh pr create --title "..." --body "..."
```

## 9. Clean up on `main`

```bash
git checkout main
```

Make the *same* removal directly on `main` — delete the same `queue.md` bullet, or `git rm` the
same whole-file task — and `git commit` it. **Do not `git push origin main`.** This local-only
commit is what keeps the next iteration (which starts back at step 2, reading `main`) from
re-picking the same task while the PR is still pending review; pushing to `main` itself stays a
human decision. See `docs/adrs/00185-software-factory-main-cleanup-commits-stay-local.md`.

## 10. Clear the in-progress marker

`rm -f docs/plans/auto/.factory_in_progress.tmp`. It's gitignored, so this needs no commit.

## 11. Report and stop

Summarize what was built and the PR link, then end your turn normally. The `onAgentTurnEnd` hook
will see the now-clean working copy: if `docs/plans/auto/` still has pending work, it resets the
session and starts this skill again for the next task; if not, it leaves the session alone.
