#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# SessionStart hook for Claude Code cloud/remote agent environments.
# Ensures the environment is fully initialized at the start of each remote session.
#
# This script is registered as a SessionStart hook in .claude/settings.json.
# It is a no-op outside cloud environments.

set -euo pipefail

# Only run in a Claude Code remote agent environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Claude session start (cloud setup) ==="

# Claude env requires explicit 'python3.12' executable name rather than relying on
# 'python3' default for python commands in Makefile setup.
PYTHON_VER=$(cut -d. -f1-2 "$REPO_ROOT/klorb/.python-version")

cd "$REPO_ROOT"
PYTHON="python${PYTHON_VER}" make cloud_setup

echo "=== Session start complete ==="
