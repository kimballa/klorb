# 2026-08-10: Software factory's `main`-branch cleanup commits stay local, never pushed

## Question

After software factory mode finishes a task on its feature branch and opens a PR, the completed
task (a `queue.md` bullet, or a whole task file) must also be removed from `main` directly —
otherwise the next autonomous iteration, which always starts by reading `main`
(`docs/specs/software-factory.md`), could pick the same task again before the PR merges. Should
that `main`-branch cleanup commit also be pushed to the remote, so `main` on GitHub reflects it
immediately?

## Answer

No. The `enable-software-factory` skill's step 9 runs `git commit` for the cleanup on `main`,
but never `git push origin main`. The commit stays local.

## Reasoning

The problem this cleanup step solves — the next local iteration re-reading `main` — is fully
solved by a local commit alone; nothing about it requires the remote to be updated. Pushing
directly to `main`, bypassing PR review, is a materially different and more consequential action
than a local-only commit: it changes what every other clone of this repo sees as `main`'s state,
including the repo's own CI and any other reviewers, for a change nobody but the software-factory
loop looked at. Keeping `main`-pushes a decision a human makes deliberately (whether by pushing
these local commits themselves later, or by some other reconciliation) avoids stacking an
unreviewed push on top of an already-unattended loop, while still letting the loop make forward
progress on its own between tasks.
