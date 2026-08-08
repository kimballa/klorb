# Parse bash commands by shelling out to `shfmt --to-json`, not Go bindings or a pure-Python parser

* Date: 2026-07-07 10:00
* Question: `BashTool` needs a real AST of a model-requested shell command — regexp/lexical
  classification is explicitly ruled out (see
  docs/plans/ready/004-bash-permissions-and-bash-tool.md's "Things *not* to use"). `mvdan/sh`'s
  Go `syntax` package is the most complete, battle-tested bash parser available. klorb is a pure
  Python project with no existing Go/cgo toolchain or FFI story. How should Python code obtain
  the AST that package produces?
* Answer: Shell out to the `shfmt` CLI (built on `mvdan/sh`'s `syntax` package) with `--to-json`,
  via the `shfmt-py` pypi package, which bundles a prebuilt `shfmt` binary as a "scripts"-only
  wheel (no importable Python API — see `klorb.permissions.shell_parse._resolve_shfmt_command`).
  `klorb.permissions.shell_parse.parse_command` runs it as a `subprocess`, parses the JSON with
  the stdlib `json` module, and walks the resulting tree. `pyproject.toml` pins `shfmt-py` to an
  *exact* version (`== 4.0.0`), not an open-ended range.
* Reasoning: A pure-Python reimplementation of bash's grammar would be a large, ongoing
  maintenance burden with no guarantee of matching `mvdan/sh`'s correctness on the exact corner
  cases (quoting, expansion classes, redirection operators) this feature's safety model depends
  on getting right — reinventing a security-relevant parser is exactly the kind of thing this
  plan's "Things not to use" section warns against by extension. Go-to-Python FFI bindings
  (cgo/ctypes bridging into `mvdan/sh` directly) would avoid the subprocess overhead but add a
  compiled-artifact-per-platform build/packaging story klorb doesn't have today, for a tool that
  is not on any hot path (`BashTool` calls are infrequent relative to model round trips, and a
  subprocess invocation is milliseconds). Shelling out is explicitly called out as an acceptable
  pattern elsewhere in klorb's own permissions work (`klorb.tui.shell.UserShellCommand` already
  launches real subprocesses; nothing in this codebase avoids `subprocess` on principle), and
  there is existing prior art for exactly this shape:
  [`oryband/claude-code-auto-approve`](https://github.com/oryband/claude-code-auto-approve) feeds
  Claude Code's own bash commands through `shfmt --to-json`, walked with `jq`, for the same
  segment-by-segment approval purpose.

  The exact-version pin matters because `shfmt-py`'s own package version doesn't map 1:1 to the
  `shfmt` version it bundles — a routine `pip install --upgrade` could silently change the
  bundled `shfmt` version and trip `mvdan/sh` issue #1321's documented `--to-json` output-shape
  drift across versions, with no corresponding klorb code change to blame. The walker in
  `klorb.permissions.shell_parse` fails closed (escalates to "ask") on any AST node shape it
  doesn't recognize, so a version drift degrades to "commands ask more than expected" rather
  than silently misclassifying something as safe — but the exact pin still avoids that surprise
  entirely for as long as a maintainer hasn't deliberately re-verified a newer `shfmt` version's
  output shape.
