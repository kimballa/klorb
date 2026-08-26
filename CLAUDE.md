@AGENTS.md

--------

The content in AGENTS.md (inlined above) is equally important as the content in this file —
do not treat it as lower-priority background. This file only adds Claude-specific advice /
overrides that are particular to Claude Code, on top of everything AGENTS.md already says.

## Mandatory comment/docstring trim pass

AGENTS.md's comment-brevity rules ("Comments and docstrings" section) are known but still get
violated in practice: each trailing " -- " clause, "e.g." aside, or second sentence feels
individually justified while drafting, even though the rule is being actively recalled.
Knowing the rule is insufficient; treat it as a required pass, not a style to keep in mind.

Before treating any task that added or edited a docstring/comment as done — after lint/
typecheck/tests pass, before committing — re-read every new or changed docstring/comment one at
a time. For each one with more than one sentence, or with a trailing " -- " clause, delete
everything past the first sentence unless removing it would leave a genuine open question a
reader would otherwise hit — not "less complete," not "less helpful context," an actual
question. Default to cutting.

### Hard bans — delete on sight, no exceptions

A module/file-level docstring is exactly one sentence. The only permitted second sentence is a
bare `See docs/specs/<file>.md.` pointer to a spec file that actually exists — nothing else, and
never a third sentence.

A `--` (em dash or double hyphen) that introduces a trailing clause counts as starting a new
sentence: delete it and everything after, don't just shorten it. This is a hard rule, not a
style preference — apply it even when the deleted clause feels informative.

The following constructs are banned outright in any docstring or comment. Don't shorten them —
delete the whole clause or sentence they appear in:

* "called by ..." / "used by ..." (documenting a caller instead of the thing itself)
* "(see ...)" or bare "see ..." cross-references to other code
* "shared by ..." (documenting other consumers instead of the thing itself)
* "mirroring ..." / "matching ..." / "the same way ... does" (justifying by comparison to
  another function instead of describing this one)
* "(e.g. ...)" and "(unlike ...)" parenthetical asides
* Any other parenthetical whose content isn't the definition itself (i.e. it's an example,
  comparison, or cross-reference rather than integral to what's being described)
* A trailing "never ..." / "not ..." / "doesn't ..." clause tacked onto an otherwise complete
  sentence to rule out some alternative. State the positive fact and stop; don't append the
  negative space around it. "The raw text exactly as posted, never rewritten" is banned --
  "The raw text exactly as posted" is the whole sentence.

## Cloud / Remote Agent Behavior

* The environment variable `CLAUDE_CODE_REMOTE` is set to the literal string `"true"` when
  Claude Code is running as a remote agent (e.g., a claude.ai cloud agent). It is unset or
  set to another value during interactive terminal sessions.
* When `CLAUDE_CODE_REMOTE=true`, submit completed work as a pull request using the `gh` CLI
  rather than presenting changes interactively:

  ```bash
  gh pr create --title "..." --body "..."
  ```

* **Never push directly to `main`.** Always work on a named feature branch and open a PR.
* When running as an interactive Claude Code terminal session (`CLAUDE_CODE_REMOTE` is not
  `"true"`), do **not** submit a PR automatically — present your changes to the user for review.
* A remote session has no `gh` CLI or direct GitHub API access (`gh`/raw HTTPS calls to
  `api.github.com` fail with "GitHub access is not enabled for this session"), and the GitHub MCP
  tools don't cover every endpoint (e.g. direct commit-page comments, as opposed to PR review
  comments). When you hit data the MCP tools can't reach, ask the user to open the API URL in
  their own browser and paste the JSON back, rather than working around it with a headless
  browser or other scripted fetch.
* If the cloud environment is broken or incomplete, you should immediately run `make cloud_setup` from the root of the project repository then retry whatever you were last doing.

## Important Rules for using tools and bash shell commands

The following are **critical** instructions for invoking shell commands:

* It is important that you be able to operate autonomously. To do so, you must adhere to
  approved bash shell commands.
* All the commands necessary to perform the full software development / test / review loop
  are already pre-approved. You should not need per-tool-call approval from the user.
* Do not pipe the output of one command directly to another; doing so can void prior approval.
  (The following are examples of forbidden patterns: `command1 | grep <pattern>` or
  `command1 | jq <expr>`). Direct the output of `command1` in each case into a temp file
  and then read it into the second command from the file.
* Do not redirect stderr to stdout with `2>&1`. You can read both output streams.
* Do not use env variable substitution. This wastes time on automatic command approval.
* Do not quote special characters like `#` or `"` or `'` or `|`, as doing so voids prior
  command approval. Instead, write such expressions into a temp file and use files as
  arguments.
* Do not use subshells with `$(...)` or backtick-quoted strings as these void prior
  approval. Run the would-be subshell command first and save its output to a file, and
  then read it in to the chained command, or read the file yourself and reproduce the
  output in an environment variable for a second command if needed.
* Do not pipe commands into `tail` in order to save tokens. Commands required for
  SDLC verification generally produce minimal output beyond what you would otherwise need
  to read anyway.

Examples of GOOD bash commands:

* `make lint typecheck test`
* `make test`
* `make TEST_SUITE=session_config test`

Examples of BAD bash commands:

* `PYTHONPATH=./src:./tests venv/bin/pytest -q tests/ 2>&1 | tail -30`
* `make test | tail -30`
* `make TEST_SUITE='test_method or test_other' test` (the `'...'` quoting around a multi-word
  `-k` expression voids prior approval same as any other quoted argument; pick a single
  substring that needs no quoting instead, e.g. `TEST_SUITE=session_config`)

When you make up complex commands, you waste more time waiting for user approval than if
you had just stuck to using the pre-approved "make" commands, even if `make test`, etc,
would run a larger number of tests or typecheck more files than an alternative you can
generate. CPU time is fast. User effort is slow. The user is very sad when you make him
proofread bash statements if a clean alternative was already provided for you.

## Test suite guidance

`klorb/`'s full `make test` run takes a few minutes. Within a dev loop, run `make
TEST_SUITE=<keyword> test` against the suite(s) covering the code you're touching, as often as
you like. Run one unscoped `make test` at the end, before treating the task as done — that full
pass is still the bar for "work is not done" in AGENTS.md, not the scoped runs along the way.
