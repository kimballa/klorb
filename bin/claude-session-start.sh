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

# Claude Code's cloud/remote harness does not surface this hook's stdout/stderr back to
# Claude, which makes failures here very difficult to debug. Capture all output to
# well-defined log files instead. Override these env vars on the CLI invocation to choose
# different paths.
CLAUDE_SESSION_START_STDOUT="${CLAUDE_SESSION_START_STDOUT:-/tmp/claude-session-start.stdout.log}"
CLAUDE_SESSION_START_STDERR="${CLAUDE_SESSION_START_STDERR:-/tmp/claude-session-start.stderr.log}"
exec >"$CLAUDE_SESSION_START_STDOUT" 2>"$CLAUDE_SESSION_START_STDERR"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Claude session start (cloud setup) ==="

# Claude env requires explicit 'python3.12' executable name rather than relying on
# 'python3' default for python commands in Makefile setup.
PYTHON_VER=$(cut -d. -f1-2 "$REPO_ROOT/klorb/.python-version")

cd "$REPO_ROOT"
PYTHON="python${PYTHON_VER}" make cloud_setup

echo "=== Session start complete ==="
