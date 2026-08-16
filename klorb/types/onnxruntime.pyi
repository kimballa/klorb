# © Copyright 2026 Aaron Kimball
"""Minimal local type stub for the `onnxruntime` package (PyPI: onnxruntime /
onnxruntime-directml / onnxruntime-gpu, all sharing this importable module name), which ships no
`py.typed` marker and has no published `types-onnxruntime` stub package. Covers only the
`get_available_providers()` surface `klorb.search_index.embedding` uses to detect GPU execution
providers."""


def get_available_providers() -> list[str]: ...
