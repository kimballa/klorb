---
name: concise-commit-and-pr-summaries
description: Calibrate the length and content of a commit message or PR description before writing one. Use whenever about to write a commit message body, a PR "Summary"/"Test plan" section, or update a PR description — especially after a multi-step task that touched many files or fixed several incidental bugs along the way, where the instinct is to narrate everything that happened.
---

# Writing concise commit messages and PR summaries

The default failure mode is narrating the diff: one bullet per rule/file/decision, sub-bullets
explaining the reasoning behind each one, and a paragraph-per-bug account of everything that got
fixed along the way. The diff already shows all of that in full, reviewable detail. The message's
job is to orient a reader in a few seconds, not re-derive the diff in prose.

## Worked example: what actually shipped

klorb PR #60 (wiring `markdownlint-cli2` into `make lint`) is a real before/after. The PR body as
originally drafted:

```text
## Summary

* Installs `markdownlint-cli2` via npm in `make cloud_setup`.
* Adds `.markdownlint-cli2.jsonc`: `fix`, `gitignore`, `noBanner`, `noProgress`, plus rule
  overrides tuned to this repo's existing conventions:
  * `MD013` (line-length) off — docs use long, soft-wrapped prose lines.
  * `MD041` (first-line-heading) off — CLAUDE.md opens with an `@AGENTS.md` import directive...
  * `MD004`/`MD048`/`MD049`/`MD050` pinned to ... to match the repo's existing majority style...
  * `MD025`/`MD033`/`MD040` are all left on — see below.
* Adds a top-level `make lint` target: lints `docs/**/*.md` + root `*.md`, then delegates...
* Adds a markdownlint step to `klorb/Makefile`'s `lint` target, scoped to `klorb.resources`...
* Adds `davidanson.vscode-markdownlint` to `.vscode/extensions.json`.
* Removes the now-completed "find a markdown linter" bullet from `TODO.md`.

Running the linter in fix mode surfaced (and, where the autofixer's mechanical rewrite would
have been wrong, required hand-fixing) a handful of pre-existing Markdown bugs:

* Two ADRs had prose lines starting with `+`/`*` that were ambiguously parsed as list markers...
* Several `<Tag>`-style placeholders weren't in backticks and were being silently swallowed...
* Every previously-untagged fenced code block now carries an explicit language...
* `default_sys.md`'s worked example used a literal `1) / 2) / 3)` numbered sequence...
* `AGENTS.md` and `TODO.md` each had more than one top-level heading...

## Test plan

* [x] `make lint` passes cleanly from the repo root (python lint + both markdownlint
      invocations).
* [x] `markdownlint-cli2 --config ...` passes from `klorb/`.
* [x] Manually diffed every changed file to confirm the autofixer's rewrites preserved
      meaning (and hand-fixed the cases above where it didn't).
```

What the repo owner actually kept after editing it down before merging:

```text
## Summary

* Installs `markdownlint-cli2` via npm in `make cloud_setup`.
* Adds `.markdownlint-cli2.jsonc`
* Adds a top-level `make lint` target
* Adds a markdownlint step to `klorb/Makefile`'s `lint` target
* Fix existing md lint bugs throughout.

## Test plan

* [x] `make lint` passes cleanly from the repo root
* [x] Manually diffed every changed file to confirm the autofixer's rewrites preserved
      meaning
```

Note what disappeared entirely, not just shortened: the per-rule rationale sub-bullets (that
reasoning already lives as comments in `.markdownlint-cli2.jsonc` — restating it in the PR body
is a second copy that will drift), the bug-by-bug narrative (collapsed to one line — the diff is
the actual record), and the two smallest accompanying changes (the vscode extension recommendation
and the `TODO.md` bullet removal) — small enough to be self-evident from skimming the diff, not
worth their own bullet.

## Commit messages: 1-2 sentences, not a changelog

The project convention (see the commit-message instructions in the system prompt) is explicit:
"a concise (1-2 sentences) commit message that focuses on the *why* rather than the *what*." A
commit body with four paragraphs — one per file touched, one per category of bug fixed — violates
that even when every sentence is accurate. If a change needs more than 1-2 sentences to justify,
that need is a signal the *code* should carry the explanation (a code comment, a spec, an ADR),
not that the commit body should grow to compensate.

## What earns a line, and what doesn't

* One bullet per **user-visible change or target** (a new Makefile target, a new config file, a
  new dependency). Not one bullet per *decision inside* that change — rule-by-rule rationale,
  per-file rewording choices, and similar reasoning belong in code/config comments, where they
  stay next to the thing they explain instead of drifting out of sync with a static PR body.
* Enumerate individual bugs found and fixed only when a reviewer needs bug-by-bug detail to judge
  risk (e.g., a security fix where each instance matters). Otherwise, one summary line ("fixed
  existing lint violations throughout") plus the diff itself is enough — don't re-narrate what
  `git diff` already shows.
* Skip a bullet entirely for changes small and self-evident enough that a reviewer skimming the
  diff wouldn't need it flagged (a one-line config tweak riding along with the main change, a
  now-stale backlog bullet removed). Reserve bullets for things a reviewer would otherwise have to
  hunt for.
* Test plan: one short line per check. No parenthetical elaboration of what each check covers —
  the check's own name should already say that.

## What still earns a line even when it's inconvenient

Brevity is not a license to bury risk. Anything that changed *behavior* beyond the stated
feature — hand-fixed content where an automated tool would have corrupted it, a decision to leave
a safety-relevant rule enabled rather than disabling it, a destructive or hard-to-reverse
operation — still gets called out explicitly, even in an otherwise terse summary. The goal is
*signal density*, not minimum length: cut the restated rationale and the bug-by-bug travelogue,
never the one sentence a reviewer actually needs to evaluate risk.

## Checklist before sending a commit message or PR body

* [ ] Is each Summary bullet one line naming a change, not a change plus its internal rationale?
* [ ] Did I enumerate individual bugs/files where one summary line (trusting the diff) would do?
* [ ] Did I drop bullets for small, self-evident accompanying changes?
* [ ] Is the commit message itself 1-2 sentences, focused on *why*?
* [ ] Does anything genuinely risky or non-obvious still get its own explicit line?
