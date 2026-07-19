# (c) Copyright 2026 Aaron Kimball
#
# klorb top-level control Makefile.
#
# * `make help` for all commands

SHELL:=/bin/bash
APT_GET=sudo apt-get

COMMANDS=help cloud_setup

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
	source "$$HOME/.cargo/env" && cargo install chainlink-tracker
	$(MAKE) -C klorb PYTHON=$(PYTHON) venv install_dev_deps init

.PHONY: ${COMMANDS}
