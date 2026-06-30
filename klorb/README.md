
# klorb

klorb is your friendly neighborhood agent.

## Setup

From this directory (`klorb/`):

```
make venv
make install_dev_deps
```

* `make venv` creates a local `venv/` virtualenv (using `$PYTHON`, default `python3`) and
  installs `uv`, which the other targets use to install dependencies.
* `make install_dev_deps` installs both runtime and development dependencies (test, lint,
  and typecheck tooling) from `dev-requirements.txt` into `venv/`.
* For a runtime-only install (no dev tooling), use `make install_deps` instead, which reads
  `release-requirements.txt`.
* After adding or changing a dependency in `pyproject.toml`, run `make sync_deps` to
  regenerate `dev-requirements.txt` and `release-requirements.txt`, then
  `make install_dev_deps` to install the updated lock.
* `make lint typecheck test` runs the full local CI suite. See `make help` for all targets.

## Usage

```
klorb                                       # starts the interactive REPL
klorb -m "What is the capital of France?"   # single-shot prompt/response, no REPL
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

See [`usage.md`](usage.md) for the full command reference, including the
`--session-log`/`--no-session-log` flags and supported environment variables.
