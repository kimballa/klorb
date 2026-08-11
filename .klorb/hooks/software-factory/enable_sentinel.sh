#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# onActivateSkill handler for /enable-software-factory. Creates the sentinel file that turns
# software-factory mode on. Filenames here must match queue_utils.py's ENABLE_SENTINEL_NAME /
# AUTO_DIR_RELATIVE constants.
set -euo pipefail

AUTO_DIR="$WORKSPACE_ROOT/docs/plans/auto"
mkdir -p "$AUTO_DIR"
touch "$AUTO_DIR/.enable_software_factory.tmp"
printf '{"success": true}'
