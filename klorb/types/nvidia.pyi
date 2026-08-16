# © Copyright 2026 Aaron Kimball
"""Minimal local type stub for the `nvidia` namespace package (the various `nvidia-*-cuXX` CUDA
runtime pip packages, e.g. `nvidia-cublas`, `nvidia-cudnn-cu13`), which ships no `py.typed`
marker and has no published stub package. Empty: `klorb.search_index.embedding.
_preload_nvidia_cuda_libraries` only needs `import nvidia` to type-check and reads `__file__`,
which mypy already assumes every module has."""
