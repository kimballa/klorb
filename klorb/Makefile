# (c) Copyright 2026 Aaron Kimball
#
# klorb Makefile
#
# * `make help` for all commands
# * `make lint typecheck test` to run all CI tests.
# * `make install_dev_deps` to install local deps, `install_deps` for production
#   * `make sync_deps` to resolve and update dependency locks from the pkg manager.

COMMANDS=help lint typecheck clean distclean sync_deps \
	install_deps install_dev_deps test evals venv init

PYFLAKES=venv/bin/flake8
FLAKE8RC=`pwd`/.flake8
MYPY=venv/bin/mypy
DMYPY=venv/bin/dmypy
PIP=venv/bin/pip
PYTEST=venv/bin/pytest
SHELL=/bin/bash
DOCKER=docker
UV=venv/bin/uv
LOAD_VENV=export VIRTUAL_ENV="$(CURDIR)/venv"
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

PYTHONPATH=./src/:./tests/
MYPYPATH=./types/

# e.g. to run a single test, use: `TESTARGS='-k one_specific_test_name --show-capture=log' make test`
#
# ... if you want to see the logger output even if the test succeeded, add `-rP`.
#
# The --stepwise flag exits on first test fail rather than run them all; then next run
# it picks up where it left off.. Hunt thru fewer logs. If the previous full run was
# clean, start from the beginning (--lfnf=all).
#
# We only show the logger output, not stdout/stderr since those are redundant with the log.
TESTARGS ?=--stepwise --lfnf=all --show-capture=log

# Extra args forwarded to `evals/run_evals.py`, e.g. `EVALARGS='--model openai/gpt-4o' make evals`.
EVALARGS ?=

help:
	@echo "Available commands:"
	@echo "${COMMANDS}"


lint:
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" src/
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" tests/
	$(LOAD_VENV); "${PYFLAKES}" --config "${FLAKE8RC}" evals/

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
	@echo ""
	@echo "Runtime and development dependencies calculated."
	@echo "Use 'make install_deps' to install runtime dependencies."
	@echo "Use 'make install_dev_deps' to install runtime and dev dependencies."

install_deps: venv
	# Only install release requirements.
	@echo "Installing runtime dependencies..."
	$(LOAD_VENV); "$(UV)" pip install -r "$(RELEASE_REQUIREMENTS)"
	@echo ""
	@echo "Runtime dependencies installed."
	@echo "For development, run: make install_dev_deps"

install_dev_deps: venv
	$(LOAD_VENV); "$(UV)" pip install -r "$(LOCAL_REQUIREMENTS)"
	@echo ""
	@echo "Installed runtime and development dependencies."

# Bootstrap a klorb-config.json, a klorb executable symlink, and the bundled tiktoken cache
# for this venv's own klorb (see docs/specs/klorb-init.md). `--force` overwrites an existing
# config file/symlink rather than leaving it alone, so re-running this target (e.g. a repeated
# cloud_setup) always ends up with the freshly-installed version's files.
init: venv
	$(LOAD_VENV); "$(KLORB_BIN)" init --force

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
	$(LOAD_VENV); PYTHONPATH="$(PYTHONPATH)" "$(PYTEST)" -o cache_dir=.pytest_cache tests/ $(TESTARGS)

# Tool-efficacy evals: makes real calls to a real hosted model (see docs/specs/tool-eval-harness.md,
# docs/adrs/tool-evals-skip-without-api-key.md).
evals:
	$(LOAD_VENV); PYTHONPATH="$(PYTHONPATH)" "$(VENV_PYTHON)" -m evals.run_evals $(EVALARGS)


.PHONY: ${COMMANDS}
