#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# onProcessEnd handler. Removes both software-factory sentinel files unconditionally, so a
# crashed or killed process never leaves a stale in-progress marker for the next one. `rm -f`
# so a missing file never produces a nonzero exit. Filenames here must match queue_utils.py's
# ENABLE_SENTINEL_NAME / IN_PROGRESS_SENTINEL_NAME / AUTO_DIR_RELATIVE constants.
set -euo pipefail

AUTO_DIR="$WORKSPACE_ROOT/docs/plans/auto"
rm -f "$AUTO_DIR/.enable_software_factory.tmp" "$AUTO_DIR/.factory_in_progress.tmp"
printf '{"success": true}'
