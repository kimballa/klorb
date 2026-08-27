#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# Installs the Rust toolchain (rustup, cargo, rustc) via rustup's official installer script, then
# adds the Linux x86_64 and aarch64 targets vnd/Makefile cross-compiles chainlink for.
# Invoked by the top-level Makefile's cloud_setup target.
#
# Skips the rustup-init download, silently and successfully, if cargo is already on PATH — e.g. a
# container image that already bundles a Rust toolchain — so re-running cloud_setup never
# redundantly reinstalls it or fails on a network policy that blocks sh.rustup.rs.

set -euo pipefail

if ! which cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs > /tmp/rustup-init
  chmod a+x /tmp/rustup-init
  /tmp/rustup-init -y --profile minimal
fi

if [ -f "$HOME/.cargo/env" ]; then
  source "$HOME/.cargo/env"
fi

if which rustup >/dev/null 2>&1; then
  rustup target add x86_64-unknown-linux-gnu
  rustup target add aarch64-unknown-linux-gnu
fi
