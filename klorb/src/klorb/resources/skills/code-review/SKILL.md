---
name: code-review
description: >
  Review a code change -- the working copy's uncommitted diff, the latest commit, every commit on
  a non-mainline branch, or a GitHub pull request by id/url -- for logic bugs, architectural fit,
  security, and (when one exists) conformance to its plan or spec, then deliver a prioritized
  findings report. Use when asked to review, audit, or check a change before it merges.
---

# Reviewing a code change

Your job is to audit a change and report what you find -- not to rewrite it. Converge on "this is
good enough to merge" whenever the evidence supports it; don't manufacture findings to have
something to report. A clean bill of health is a legitimate, useful outcome.

## 1. Determine what's under review

Figure out the target before doing anything else:

- No target given, working copy has uncommitted changes -- review those (`git status`,
  `git diff HEAD`).
- No target given, working copy is clean -- review the most recent commit (`git show HEAD` /
  `git log -1 -p`).
- On a branch that has diverged from the mainline (`git merge-base main HEAD` differs from
  `HEAD`) -- review every commit on the branch (`git diff <merge-base>..HEAD`). `git fetch`
  first if the local mainline ref might be stale, and `git branch -vv` to confirm what the
  branch is tracking.
- The user names a PR by number or URL -- use `gh pr diff <number>` (or the URL directly) to get
  the diff, and `gh pr view <number> --json title,body,baseRefName` for its description and base
  branch. `gh pr checkout` only if you need a working copy to run tests against, not just to read
  the diff.

If it's ambiguous which of these applies, ask rather than guess.

## 2. Extract the diff

Redirect the diff to a plain file in the workspace (e.g. `git diff HEAD > review.patch`, or
`gh pr diff <number> > review.patch`) rather than re-deriving it from scratch for every
subagent below -- one extraction, several readers. Treat it as a throwaway artifact: delete it
once the review is done, the same as any other scratch file.

Skim it yourself first to get the shape of the change (which files, roughly how large) before
delegating -- you can't write a good subagent prompt about a diff you haven't looked at.

## 3. Delegate the reading to Explorer subagents

Activate `launch-explorer-subagent` for the mechanics of `CreateSubagent`. Launch these in
parallel where they don't depend on each other's findings -- each is a bounded question with its
own report:

- **Logic bugs.** Point it at `review.patch` (or paste the diff directly if it's small) and ask
  it to trace each changed function's logic for correctness: off-by-ones, wrong operators,
  unhandled branches, mismatched types, state that's mutated in the wrong order. Ask for file and
  line citations, not summaries.
- **Architectural coherence.** This one needs more than the diff: point it at the changed files'
  _surrounding_ code (class hierarchies, method signatures other callers depend on,
  encapsulation boundaries) and ask whether the patch actually fits the file it landed in, or
  fights the existing design -- a new public method that duplicates an existing one, a class
  reaching into another's internals, a signature that doesn't match its siblings.
- **Security.** Ask it to look for injection (shell, SQL, path traversal), trust boundaries
  crossed without validation, secrets or credentials handled carelessly, and newly-introduced
  attack surface -- with the same file/line citation requirement.
- **Plan or spec conformance**, only if one exists. If the change references a
  `docs/plans/PLAN-*` document, a spec under `docs/specs/`, or a set of tracked tasks, launch an
  Explorer to compare what the plan called for against what actually landed, and report gaps in
  either direction (unimplemented pieces, or scope that crept in beyond the plan).

Give each subagent a precise, bounded question (see `launch-explorer-subagent`'s "Composing the
initial message") and a citation requirement -- a report that says "there might be an issue in
the auth code" is not useful; "auth.py:42 checks `token.valid` before `token.expiry`, so an
expired-but-otherwise-valid token passes" is.

Wait for each explorer's report (`WaitForSubagent`). This may time out after 120 seconds. Code
review will take several minutes. Continue to wait patiently by invoking `WaitForSubagent`
multiple times in a row, if necessary, even if it says the tool call "failed" due to timeout. You
launched subagents for a reason; avoid the impulse to do a hasty and less-thorough version of their
work yourself. That's wasteful. They will provide you with a report if you are patient.

## 4. Confirm suspected bugs yourself

A subagent's report is a lead, not a verdict. Before including a finding in your report, get
your own evidence for it -- the same discipline as `debug-with-evidence`: state the specific
failure you think occurs, then check it rather than reasoning about it further.

- Read the flagged code yourself.
- If a bug is plausible but not obviously certain, reproduce it: a throwaway script, a temporary
  test, or a small deliberate edit that would make an existing test catch the bug if your theory
  is right. Run the project's real test/lint/typecheck commands to confirm or rule things out --
  don't shell out to ad hoc tooling the project doesn't use.
- Unwind anything temporary you added to confirm a finding -- a probe edit, a scratch script, an
  instrumented log line -- before you finish. You're auditing the patch, not contributing to it;
  the working tree should be exactly as you found it, plus nothing.
- Only report a finding once you're confident it's real (roughly: you could explain the concrete
  input or state that triggers it, not just that the code "looks wrong").

## 5. Check test coverage

For each changed or added method/class, check whether the test suite actually exercises it --
not just the happy path. Ask specifically:

- Is there a test at all for new logic, or only for the code paths it happened to pass through?
- Do the tests cover the edge cases the change itself makes newly possible -- empty input, the
  boundary of a new conditional, an error path a new branch introduced?
- Did an existing test get weakened (a loosened assertion, a skipped case) to make the change
  pass, rather than the change being made to satisfy the existing test?

## 6. Report

Organize findings most-severe first: security and data-integrity issues, then functional bugs,
then gaps between an implementation and its plan/spec, then anything smaller. For each finding,
state the concrete failure scenario (what input or sequence triggers it), not just a description
of the code. Say plainly whether anything blocks merging. If nothing survived step 4's
confirmation, say the patch looks good and why you're confident -- that is a complete review, not
an incomplete one.
