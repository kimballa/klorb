# (c) Copyright 2026 Aaron Kimball
#
# klorb top-level control Makefile.
#
# * `make help` for all commands

SHELL:=/bin/bash
APT_GET=sudo apt-get
NPM=npm

COMMANDS=help cloud_setup lint lint_docs typecheck sync_deps \
	install_deps install_dev_deps test clean distclean

# Python executable to use when creating the venv. Can be overridden on the command line
# (e.g. PYTHON=python3.12 make cloud_setup) or via the cloud session-start script.
PYTHON ?= python3

help:
	@echo "Available commands:"
	@echo "${COMMANDS}"

# Perform setup steps needed to set up shop in an ephemeral cloud env for development.
cloud_setup:
	# Install system dependencies
	$(APT_GET) update -qq || true
	$(APT_GET) -y --fix-missing install bubblewrap curl
	./bin/install_rust.sh
	$(NPM) install -g markdownlint-cli2
	$(NPM) install -g jsonlint
	$(MAKE) -C klorb PYTHON=$(PYTHON) venv install_dev_deps init
	$(MAKE) -C vscode-plugin install_dev_deps

# Lint documentation Markdown (docs/ and the root-level agent-instruction files), then
# delegate to klorb/'s and vscode-plugin/'s own lint targets (Python lint plus
# klorb.resources' Markdown, and the vscode-plugin's eslint pass).
lint: lint_docs
	$(MAKE) -C klorb lint
	$(MAKE) -C vscode-plugin lint

lint_docs:
	markdownlint-cli2 "docs/**/*.md" "*.md"

typecheck:
	$(MAKE) -C klorb typecheck
	$(MAKE) -C vscode-plugin typecheck

sync_deps:
	$(MAKE) -C klorb PYTHON=$(PYTHON) sync_deps
	$(MAKE) -C vscode-plugin sync_deps

install_deps:
	$(MAKE) -C klorb PYTHON=$(PYTHON) install_deps
	$(MAKE) -C vscode-plugin install_deps

install_dev_deps:
	$(MAKE) -C klorb PYTHON=$(PYTHON) install_dev_deps
	$(MAKE) -C vscode-plugin install_dev_deps

test:
	$(MAKE) -C klorb test
	$(MAKE) -C vscode-plugin test

clean:
	$(MAKE) -C klorb clean
	$(MAKE) -C vscode-plugin clean

distclean:
	$(MAKE) -C klorb distclean
	$(MAKE) -C vscode-plugin distclean

.PHONY: ${COMMANDS}
