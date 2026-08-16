
# klorb

klorb is your friendly neighborhood agent.

## Setup

From this directory (`klorb/`):

```bash
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
* For GPU-accelerated embedding on Linux/Windows, run `../bin/install-cuda-nvidia.sh` after
  the above -- see the top-level [`README.md`](../README.md#gpu-accelerated-embedding-linuxwindows).
  Not needed on macOS (Core ML support ships in the default `onnxruntime` package).

## Usage

```bash
klorb                                       # starts the interactive REPL
klorb -m "What is the capital of France?"   # single-shot prompt/response, no REPL
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

See [`usage.md`](../docs/user/usage.md) for the full command reference, including the
`--session-log`/`--no-session-log` flags and supported environment variables.

## Testing

```bash
make test                              # full suite, resumes from the last failure on rerun
make TEST_SUITE=session_config test    # only tests matching a pytest -k substring/expression
make TEST_ARGS='-rP -x' test           # extra args forwarded to pytest verbatim
```

`TEST_SUITE` is forwarded to pytest as `-k`, so it matches by substring against test, class, and
module names; combine terms with pytest's `-k` boolean syntax (`TEST_SUITE='session_config or
hooks'`). Run a scoped `TEST_SUITE` while iterating on a change, then a single unscoped `make
test` before calling the work done — the full suite takes a few minutes. An unscoped run also
uses pytest's stepwise plugin: it stops at the first failure and resumes there next time instead
of re-running everything, until a clean run or `make clean` (which clears `.pytest_cache`) resets
it.

## Evals

```bash
make evals                                       # run every suite in klorb/evals/cases.py
make evals EVALARGS='--suite file-tools'         # run just one suite
make evals EVALARGS='--list-suites'              # print known suite names and exit
make evals EVALARGS='--model openai/gpt-5-nano'  # against a specific model

# Run the risk classifier tests with the risk classifier model:
make evals EVALARGS='--model openai/gpt-oss-120b:nitro --self-review --suite risk-classifier'
```

`make evals` runs `klorb/evals/`'s tool-efficacy suites: unlike `make test` (fully offline), it
hits an external API — real prompts sent to a real hosted model via OpenRouter, offering it the
real file tools, graded by inspecting the resulting file state (not the model's closing text).
This answers a different question than `make test`: whether a tool's
`name()`/`description()`/`parameters()` are actually clear enough for a model to drive
correctly, not whether its `apply()` logic is correct. Cases are grouped into named `EvalSuite`s
(today, just `"file-tools"`); `--suite <name>` runs one, `--suite all` (the default) runs every
known suite, and `--list-suites` prints the known suite names without running anything — an
unknown `--suite` name prints a reminder to use `--list-suites` and exits `1`. By default evals
run against `klorb.openrouter.DEFAULT_MODEL`; `--model`/`EVALARGS` can point at a different
model instead. It needs `OPENROUTER_API_KEY` set (in the environment, or in a `.env` file anywhere
from `klorb/` up to the repo root — `python-dotenv` finds it automatically); without one, `make
evals` prints a one-line notice and exits `0` (doesn't fail the build — see
`docs/adrs/00033-tool-evals-skip-without-api-key.md`). Each case's report line is `[PASS]`,
`[CONDITIONAL PASS]` (passed, but took more tool calls than expected — worth a look even though
it's not a hard failure), or `[FAIL]`. See `docs/specs/tool-eval-harness.md` for the full design
and how to add a new case or suite.

Use `EVAL_ARGS='--self-review'` to feed the output of the eval process back to the model to
generate a list of recommendations for improvement from the perpsective of the model
that just exercised the tools.
