# (c) Copyright 2026 Aaron Kimball
#
# klorb Makefile
#
# * `make help` for all commands
# * `make lint typecheck test` to run all CI tests.
# * `make install_dev_deps` to install local deps, `install_deps` for production
#   * `make sync_deps` to resolve and update dependency locks from the pkg manager.

COMMANDS=help lint typecheck clean distclean sync_deps \
	install_deps install_dev_deps install test test-ci evals venv init all

PYFLAKES=venv/bin/flake8
FLAKE8RC=`pwd`/.flake8
ISORT=venv/bin/isort
MYPY=venv/bin/mypy
DMYPY=venv/bin/dmypy
PIP=venv/bin/pip
PYTEST=venv/bin/pytest
SHELL=/bin/bash
DOCKER=docker
UV=venv/bin/uv
LOAD_VENV=export VIRTUAL_ENV="$(CURDIR)/venv"
JSONLINT=../bin/jsonlint
# Python executable to use when creating the venv. Can be overridden on the command line
# (e.g. PYTHON=python3.12 make venv) or via the cloud session-start script.
PYTHON ?= python3
# Major.minor version derived from .python-version (e.g. 3.12.13 -> 3.12), used for
# the venv lib subdir which is always versioned (venv/lib/python3.12/...) regardless
# of how PYTHON is spelled.
PYTHON_VERSION := $(shell cut -d. -f1-2 .python-version)
PYTHON_LIB_DIR := python$(PYTHON_VERSION)
# Python executable to use for execution inside the virtual environment.
VENV_PYTHON=$(CURDIR)/venv/bin/python3
# `klorb` console-script entry point installed into the venv by the `venv` target (see
# pyproject.toml's [project.scripts]).
KLORB_BIN=$(CURDIR)/venv/bin/klorb

PYPROJECT=pyproject.toml
# Dependencies needed at runtime:
RELEASE_REQUIREMENTS=release-requirements.txt
# Dependencies needed at runtime plus development time:
LOCAL_REQUIREMENTS=dev-requirements.txt
# GPU (CUDA) embedding dependencies -- resolved separately since onnxruntime-gpu ships no macOS
# wheel; installed on demand by bin/install-cuda-nvidia.sh, not by install_deps/install_dev_deps.
# See nvidia-requirements.in.
NVIDIA_REQUIREMENTS_IN=nvidia-requirements.in
NVIDIA_REQUIREMENTS=nvidia-requirements.txt

PYTHONPATH=./src/:./tests/
MYPYPATH=./types/

# Restrict `make test` to matching tests via pytest's -k substring expression, e.g.
# `make TEST_SUITE=session_config test` or `make TEST_SUITE='test_method or test_other' test`.
# Leave unset to run the full suite.
TEST_SUITE ?=

# Extra args forwarded to pytest verbatim, e.g. `TEST_ARGS='-rP -x' make test` (-rP also shows
# logger output for passing tests, not just failures).
TEST_ARGS ?=

# A full-suite run (TEST_SUITE unset) uses pytest's stepwise/cache plugin: it exits on the
# first failure and resumes from there next time instead of re-running everything, and starts
# from the beginning if the previous run was clean (--lfnf=all). `make clean` deletes
# .pytest_cache, which resets this resume point. A TEST_SUITE run skips stepwise -- it's
# already narrow enough to just run straight through, and stepwise's resume point is only
# meaningful relative to the same selection of tests that produced it.
ifeq ($(strip $(TEST_SUITE)),)
FORMATTED_SUITE_ARG=--stepwise --lfnf=all
else
FORMATTED_SUITE_ARG=-k "$(TEST_SUITE)"
endif

TEST_FULL_ARGS=$(FORMATTED_SUITE_ARG) $(TEST_ARGS)

# test-ci never uses stepwise, so a full run collects and reports every failing test instead of
# stopping at the first one.
ifeq ($(strip $(TEST_SUITE)),)
CI_SUITE_ARG=
else
CI_SUITE_ARG=-k "$(TEST_SUITE)"
endif

TEST_CI_ARGS=$(CI_SUITE_ARG) $(TEST_ARGS)

# Extra args forwarded to `evals/run_evals.py`, e.g. `EVALARGS='--model openai/gpt-4o' make evals`.
EVALARGS ?=--model xiaomi/mimo-v2.5 --self-review

all: lint typecheck test

help:
	@echo "Available commands:"
	@echo "${COMMANDS}"
	@echo ""
	@echo "Test selection:"
	@echo "  make test                              # full suite, resumes from last failure on rerun"
	@echo "  make TEST_SUITE=<pytest -k expr> test  # only tests matching the given substring/expression"
	@echo "  make TEST_ARGS='<pytest flags>' test   # extra args forwarded to pytest verbatim"
	@echo "  make test-ci                           # full suite, never stops early; reports every failure"


lint:
	$(LOAD_VENV); "${ISORT}" --jobs -1 --gitignore src/ tests/ evals/
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" src/
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" tests/
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" evals/
	find src tests -name "*.json" -print0 | xargs -0 --no-run-if-empty $(JSONLINT)
	markdownlint-cli2 --config ../.markdownlint-cli2.jsonc "src/klorb/resources/**/*.md"
	markdownlint-cli2 --config ../.markdownlint-cli2.jsonc "*.md"

typecheck:
	#PYTHONPATH="$(PYTHONPATH)" "$(MYPY)" --check-untyped-defs src tests
	# was `dmypy run -- --check-untyped-defs...`
	$(LOAD_VENV); \
	PYTHONPATH="$(PYTHONPATH)" MYPYPATH="$(MYPYPATH)" "$(MYPY)" --check-untyped-defs --pretty \
			--warn-unreachable \
			--warn-redundant-casts \
			--disallow-untyped-globals \
			--disallow-redefinition \
			--extra-checks \
			--disallow-untyped-calls \
			--disallow-untyped-decorators \
			--warn-return-any \
			--native-parser \
			--num-workers 4 \
			--cache-dir .mypy_cache \
			src tests evals

venv:
	[ -d venv ] || ("$(PYTHON)" -m venv venv; \
		source venv/bin/activate && "$(PIP)" install wheel)
	$(LOAD_VENV); "$(PIP)" install -U uv
	# Editable install of klorb itself so its console-script entry point
	# (venv/bin/klorb, see pyproject.toml's [project.scripts]) exists.
	$(LOAD_VENV); "$(UV)" pip install -e .

sync_deps: venv
	@echo "Resolving development dependencies..."
	$(LOAD_VENV); "$(UV)" pip compile pyproject.toml --extra dev -o "$(LOCAL_REQUIREMENTS)" -U
	@echo "Resolving runtime dependencies..."
	$(LOAD_VENV); "$(UV)" pip compile pyproject.toml -o "$(RELEASE_REQUIREMENTS)" -U
	@echo "Resolving GPU (CUDA) dependencies..."
	$(LOAD_VENV); "$(UV)" pip compile "$(NVIDIA_REQUIREMENTS_IN)" -o "$(NVIDIA_REQUIREMENTS)" -U
	@echo ""
	@echo "Runtime and development dependencies calculated."
	@echo "Use 'make install_deps' to install runtime dependencies."
	@echo "Use 'make install_dev_deps' to install runtime and dev dependencies."
	@echo "Use './bin/install-cuda-nvidia.sh' (from the repo root) for GPU embedding on Linux/Windows."

install_chainlink:
	# Install chainlink for task tracking
	source "$$HOME/.cargo/env" && cargo install chainlink-tracker

install_deps: venv install_chainlink
	# Only install release requirements.
	@echo "Installing runtime dependencies..."
	$(LOAD_VENV); "$(UV)" pip install -r "$(RELEASE_REQUIREMENTS)"
	@echo ""
	@echo "Runtime dependencies installed."
	@echo "For development, run: make install_dev_deps"

install_dev_deps: venv install_chainlink
	$(LOAD_VENV); "$(UV)" pip install -r "$(LOCAL_REQUIREMENTS)"
	@echo ""
	@echo "Installed runtime and development dependencies."

# Bootstrap a klorb-config.json, a klorb executable symlink, the bundled tiktoken cache, and
# the bundled resource data (hooks, scripts, ...) for this venv's own klorb (see
# docs/specs/klorb-init.md).
init: venv
	$(LOAD_VENV); "$(KLORB_BIN)" init || true

# Install runtime deps, then run `klorb init` so `${klorbDataDir}`-referencing config
# (`klorb.config_macros`) has real files to find.
install: install_deps init

clean:
	find src -name "*.pyo" -delete
	find src -name "*.pyc" -delete
	find tests -name "*.pyo" -delete
	find tests -name "*.pyc" -delete
	-find . -name ".mypy_cache" -exec rm -rf {} \;
	#-find . -name "__pycache__" -exec rm -rf {} \;
	-find . -name ".pytest_cache" -exec rm -rf {} \;

distclean: clean
	-rm -rf venv
	-rm .venv_init_ok

test:
	$(LOAD_VENV); PYTHONPATH="$(PYTHONPATH)" "$(PYTEST)" -o cache_dir=.pytest_cache tests/ \
		--show-capture=log $(TEST_FULL_ARGS)

# Full-suite run for CI: runs every test regardless of prior failures and reports them all,
# rather than stopping at the first.
test-ci:
	$(LOAD_VENV); PYTHONPATH="$(PYTHONPATH)" "$(PYTEST)" -o cache_dir=.pytest_cache tests/ \
		--show-capture=log $(TEST_CI_ARGS)

# Tool-efficacy evals: makes real calls to a real hosted model (see docs/specs/tool-eval-harness.md,
# docs/adrs/tool-evals-skip-without-api-key.md).
evals:
	$(LOAD_VENV); PYTHONPATH="$(PYTHONPATH)" "$(VENV_PYTHON)" -m evals.run_evals $(EVALARGS)


.PHONY: ${COMMANDS}
