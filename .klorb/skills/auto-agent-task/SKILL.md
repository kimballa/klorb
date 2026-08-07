---
name: auto-agent-task
description: >
  Pick a `(#agent)`-marked task from TODO.md, implement it on a feature branch, get an
  independent code review from a Reviewer subagent, iterate until the reviewer approves,
  remove the TODO item, and commit. Use when asked to "work on an agent task", "do a TODO
  item", "auto-implement", or similar.
---

# Auto-agent task

This skill drives an end-to-end loop: pick a `(#agent)` task from `TODO.md`, implement it,
have it reviewed, fix issues, and land it on a feature branch.

## 1. Identify the task

The user may specify a task directly (by line number, by quoting the text, or by description).
If they say "pick one" or "do the next one", scan `TODO.md` for lines containing `(#agent)` and
choose one. When choosing yourself, prefer tasks that are:

- In a "bugs" section first, then new features.
- Higher in their subsection (subsections are sorted; top-level TUI vs VSCode sections are not)
- Self-contained — no open questions or external blockers.
- Small enough to finish in one session.

Read the full task text carefully, including any indented sub-items. Note any referenced specs
(`docs/specs/...`), ADRs (`docs/adrs/...`), or plans (`docs/plans/...`) — read those before
starting implementation.

## 2. Create a feature branch

Start on the `main` branch. Use `git branch` to check where you are, and `git checkout main`
if needed to start on the main branch. Only start on some other feature branch you have
created if the task very explicitly builds as a continuation on the feature you _just_
implemented.

Name the branch with a short slug derived from the task, e.g.:

```bash
git checkout -b feat/secret-detector-bash-output
```

Use `feat/` for features, `fix/` for bugs. Keep the slug under ~50 characters.

## 3. Implement

Follow all standing project conventions (see `AGENTS.md` and any workspace instructions). Key
reminders:

- Read existing code before modifying it.
- Make the smallest change that correctly accomplishes the task.
- Add `logger.debug()` breadcrumbs at consequential moments.
- Write or update tests — the task isn't done until tests pass.
- Run `make lint`, `make typecheck`, and `make test` from the appropriate subdirectory
  (`klorb/` for harness work, `vscode-plugin/` for plugin work).
- If the task touches a spec (`docs/specs/...`), update the spec to reflect the new behavior.
- Make intermediate commits as logical units of work accumulate. Use concise commit messages.

## 4. Code review

Once implementation is complete and tests pass, launch a **Reviewer** subagent for an
independent code review. Activate the `code-review` skill for the review mechanics.

```python
CreateSubagent(
    role="reviewer",
    session_title="Review: <short task description>",
    initial_message=(
        "Review the changes on this branch compared to main. "
        "The task was: <paste the TODO.md task text>. "
        "Run `git diff main..HEAD` to see the full diff. "
        "Check for logic bugs, architectural fit, security, test coverage, "
        "and conformance to project conventions in AGENTS.md. "
        "Report findings prioritized by severity."
    )
)
```

Wait for the reviewer's report (`WaitForSubagent`). This may time out after 120 seconds. Code
review will take several minutes. Continue to wait patiently by calling `WaitForSubagent`
multiple times in a row, even if it says the tool call "failed" due to timeout. Do not attempt to do
your own self-review and pass that off as good enough. You must get the output from the subagent for
sign-off.

## 5. Fix and re-review

For each finding the reviewer reports:

1. Evaluate whether it's a real issue (apply your own judgment — the reviewer is a lead, not
   an oracle).
2. Fix real issues.
3. Re-run tests after each fix.

If you made substantive changes in response to review, launch the reviewer again. Loop until
the reviewer reports no blocking findings. Cap at 3 review rounds to avoid infinite loops —
after 3 rounds, use your judgment on remaining minor items.

## 6. Remove the TODO item

Delete the `(#agent)` task line (and its indented sub-items, if any -- assuming, of course, that
they are properly complted) from `TODO.md`. Do not leave a blank line where the item was if it would
create a double-blank. Run `make lint_docs` from the root to verify the markdown is still clean.

## 7. Final commit

Amend or create a final commit that includes the TODO.md cleanup. The commit message should
summarize the **total** work performed, not just the last fix. Example:

```
feat: apply SecretDetector to BashTool stderr/stdout

- Applied SecretDetector to BashTool's stdout and stderr output
- Added unit tests for secret masking in tool output
```

Push the branch if the user asked for it, or just leave it local and report the branch name.
