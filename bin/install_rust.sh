#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# Installs the Rust toolchain (rustup, cargo, rustc) via rustup's official installer script.
# Invoked by the top-level Makefile's cloud_setup target.
#
# Exits early, silently, and successfully if cargo is already on PATH — e.g. a container image
# that already bundles a Rust toolchain — so re-running cloud_setup never redundantly reinstalls
# it or fails on a network policy that blocks sh.rustup.rs.

set -euo pipefail

if which cargo >/dev/null 2>&1; then
  exit 0
fi

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs > /tmp/rustup-init
chmod a+x /tmp/rustup-init
/tmp/rustup-init -y --profile minimal
