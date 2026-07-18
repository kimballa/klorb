
Most standing instruction information is stored in `AGENTS.md`; read it immediately
at the start of every session, before doing any work.

This file only contains Claude-specific advice / overrides that are particular to
Claude Code; you must read AGENTS.md to get the total set of repository-specific instructions.

## Cloud / Remote Agent Behavior

* The environment variable `CLAUDE_CODE_REMOTE` is set to the literal string `"true"` when
  Claude Code is running as a remote agent (e.g., a claude.ai cloud agent). It is unset or
  set to another value during interactive terminal sessions.
* When `CLAUDE_CODE_REMOTE=true`, submit completed work as a pull request using the `gh` CLI
  rather than presenting changes interactively:
  ```
  gh pr create --title "..." --body "..."
  ```
* **Never push directly to `main`.** Always work on a named feature branch and open a PR.
* When running as an interactive Claude Code terminal session (`CLAUDE_CODE_REMOTE` is not
  `"true"`), do **not** submit a PR automatically — present your changes to the user for review.

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

Examples of BAD bash commands:
* `PYTHONPATH=./src:./tests venv/bin/pytest -q tests/ 2>&1 | tail -30`
* `make test | tail -30`

When you make up complex commands, you waste more time waiting for user approval than if
you had just stuck to using the pre-approved "make" commands, even if `make test`, etc,
would run a larger number of tests or typecheck more files than an alternative you can
generate. CPU time is fast. User effort is slow. The user is very sad when you make him
proofread bash statements if a clean alternative was already provided for you.
