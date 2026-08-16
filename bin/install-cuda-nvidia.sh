#!/usr/bin/env bash
# © Copyright 2026 Aaron Kimball
#
# Installs onnxruntime-gpu and its matching NVIDIA CUDA/cuDNN runtime packages into klorb's venv,
# enabling GPU-accelerated embedding (klorb index scan, the background workspace indexer, and
# hybrid_search query embedding) on Linux/Windows (WSL2). Not needed on macOS -- the default
# onnxruntime package there already includes CoreMLExecutionProvider. See
# docs/adrs/00196-gpu-embedding-uses-cuda-not-directml.md.
#
# Safe to re-run: installs are idempotent. onnxruntime-gpu and plain onnxruntime (a fastembed
# dependency, not a direct klorb one) both install to the same site-packages/onnxruntime/ path,
# so this explicitly uninstalls plain onnxruntime first rather than relying on install-order
# file-overwrite behavior -- see docs/adrs/00196-gpu-embedding-uses-cuda-not-directml.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KLORB_DIR="$REPO_ROOT/klorb"
VENV_UV="$KLORB_DIR/venv/bin/uv"
NVIDIA_REQUIREMENTS="$KLORB_DIR/nvidia-requirements.txt"

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "install-cuda-nvidia.sh is for Linux/Windows (WSL2) -- macOS uses CoreMLExecutionProvider" \
    "via the default onnxruntime package instead. Nothing to do."
  exit 0
fi

if [[ ! -x "$VENV_UV" ]]; then
  echo "No venv found at $KLORB_DIR/venv -- run 'make -C klorb venv' first." >&2
  exit 1
fi

if [[ ! -f "$NVIDIA_REQUIREMENTS" ]]; then
  echo "$NVIDIA_REQUIREMENTS not found -- run 'make -C klorb sync_deps' first." >&2
  exit 1
fi

export VIRTUAL_ENV="$KLORB_DIR/venv"
"$VENV_UV" pip uninstall onnxruntime
"$VENV_UV" pip uninstall onnxruntime-gpu
"$VENV_UV" pip install -r "$NVIDIA_REQUIREMENTS"

echo ""
echo "Installed GPU (CUDA) embedding dependencies. Verifying installation..."
echo ""

onnx_providers=$($KLORB_DIR/venv/bin/python -c 'import onnxruntime; print(onnxruntime.get_available_providers())' 2>/dev/null)
set +e
echo "${onnx_providers}" | grep CUDAExecutionProvider > /dev/null
grep_out=$?
set -e
echo "ONNX runtime providers available: $onnx_providers"
if [ "$grep_out" != "0" ]; then
  echo "Error: CUDA execution provider could not be detected."
  exit 1
fi
echo "Success! (CUDA execution provider detected.)"

