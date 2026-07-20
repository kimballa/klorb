
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

## Evals

```
make evals                                    # run every case in klorb/evals/cases.py
EVALARGS='--model openai/gpt-5-nano' make evals   # against a specific model
```

`make evals` runs `klorb/evals/`'s tool-efficacy suite: unlike `make test` (fully offline), it
hits an external API — real prompts sent to a real hosted model via OpenRouter, offering it the
real file tools, graded by inspecting the resulting file state (not the model's closing text).
This answers a different question than `make test`: whether a tool's
`name()`/`description()`/`parameters()` are actually clear enough for a model to drive
correctly, not whether its `apply()` logic is correct. The default model
(`klorb.openrouter.DEFAULT_MODEL`) is free to use; `--model`/`EVALARGS` can point at a different
(possibly paid) model instead, but that's not required just to run the suite. It needs
`OPENROUTER_API_KEY` set (in the environment, or in a `.env` file anywhere from `klorb/` up to
the repo root — `python-dotenv` finds it automatically); without one, `make evals` prints a
one-line notice and exits `0` (doesn't fail the build — see
`docs/adrs/tool-evals-skip-without-api-key.md`). Each case's report line is `[PASS]`,
`[CONDITIONAL PASS]` (passed, but took more tool calls than expected — worth a look even though
it's not a hard failure), or `[FAIL]`. See `docs/specs/tool-eval-harness.md` for the full design
and how to add a new case.

Use `EVAL_ARGS='--self-review'` to feed the output of the eval process back to the model to
generate a list of recommendations for improvement from the perpsective of the model
that just exercised the tools.

