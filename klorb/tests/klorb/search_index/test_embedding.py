# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.embedding."""

import os
import sys
import types
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from klorb.search_index import embedding as embedding_module
from klorb.search_index.embedding import (
    CUDA_PROVIDER,
    EmbeddingModel,
    _preload_nvidia_cuda_libraries,
    cuda_available,
)


@pytest.fixture(autouse=True)
def _isolated_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `embedding_model_target_dir()` at a real (empty) directory so `EmbeddingModel`'s
    existence check passes without needing the real bundled model on disk -- every test here
    also stubs out `fastembed.TextEmbedding` itself, so nothing tries to actually load it."""
    target = tmp_path / "embedding-model"
    target.mkdir()
    monkeypatch.setattr(embedding_module, "embedding_model_target_dir", lambda: target)


@pytest.fixture(autouse=True)
def _no_real_library_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `use_gpu=True` test stubs `fastembed.TextEmbedding` too, so there's no real CUDA
    provider to preload libraries for -- this keeps tests hermetic and fast regardless of
    whether `nvidia-*` packages happen to be installed in the environment running the suite.
    `test_preload_nvidia_cuda_libraries_*` tests the real function directly instead."""
    monkeypatch.setattr(embedding_module, "_preload_nvidia_cuda_libraries", lambda: None)


def _stub_text_embedding(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    stub = MagicMock()
    monkeypatch.setattr("fastembed.TextEmbedding", stub)
    return stub


def test_cuda_available_true_when_provider_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider", CUDA_PROVIDER])

    assert cuda_available() is True


def test_cuda_available_false_when_provider_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    assert cuda_available() is False


def test_embedding_model_defaults_to_cpu_only_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_text_embedding(monkeypatch)

    EmbeddingModel()

    assert stub.call_args.kwargs["providers"] is None


def test_embedding_model_use_gpu_raises_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_text_embedding(monkeypatch)
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider"])

    with pytest.raises(RuntimeError, match=CUDA_PROVIDER):
        EmbeddingModel(use_gpu=True)


def test_embedding_model_use_gpu_requests_cuda_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_text_embedding(monkeypatch)
    monkeypatch.setattr(
        "onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider", CUDA_PROVIDER])

    EmbeddingModel(use_gpu=True)

    assert stub.call_args.kwargs["providers"] == [CUDA_PROVIDER, "CPUExecutionProvider"]


def test_embedding_model_use_gpu_preloads_nvidia_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_text_embedding(monkeypatch)
    monkeypatch.setattr(
        "onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider", CUDA_PROVIDER])
    preload = MagicMock()
    monkeypatch.setattr(embedding_module, "_preload_nvidia_cuda_libraries", preload)

    EmbeddingModel(use_gpu=True)

    preload.assert_called_once_with()


def test_embedding_model_without_gpu_does_not_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_text_embedding(monkeypatch)
    preload = MagicMock()
    monkeypatch.setattr(embedding_module, "_preload_nvidia_cuda_libraries", preload)

    EmbeddingModel()

    preload.assert_not_called()


def test_embedding_model_use_gpu_raises_when_provider_fails_to_actually_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "onnxruntime.get_available_providers", lambda: ["CPUExecutionProvider", CUDA_PROVIDER])

    def _text_embedding_warns_fallback(**kwargs: Any) -> MagicMock:
        warnings.warn(
            f"Attempt to set {CUDA_PROVIDER} failed. Current providers: ['CPUExecutionProvider'].",
            RuntimeWarning)
        return MagicMock()

    monkeypatch.setattr("fastembed.TextEmbedding", _text_embedding_warns_fallback)

    with pytest.raises(RuntimeError, match="failed to actually load"):
        EmbeddingModel(use_gpu=True)


def test_embedding_model_passes_threads_through(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_text_embedding(monkeypatch)

    EmbeddingModel(threads=1)

    assert stub.call_args.kwargs["threads"] == 1


def test_embedding_model_raises_without_the_bundled_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedding_module, "embedding_model_target_dir", lambda: tmp_path / "missing")

    with pytest.raises(FileNotFoundError):
        EmbeddingModel()


def test_embed_passages_and_embed_query_delegate_to_the_wrapped_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_instance = MagicMock()
    stub_instance.passage_embed.return_value = iter([np.zeros(4, dtype=np.float32)])
    stub_instance.query_embed.return_value = iter([np.ones(4, dtype=np.float32)])
    monkeypatch.setattr("fastembed.TextEmbedding", lambda **kwargs: stub_instance)

    model = EmbeddingModel()

    passages = model.embed_passages(["hello"])
    assert len(passages) == 1
    stub_instance.passage_embed.assert_called_once_with(["hello"])

    query = model.embed_query("hello")
    assert query.tolist() == [1.0, 1.0, 1.0, 1.0]
    stub_instance.query_embed.assert_called_once_with(["hello"])


def test_embedding_model_sets_and_restores_hf_hub_offline_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_text_embedding(monkeypatch)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen: dict[str, Any] = {}

    def _capturing_text_embedding(**kwargs: Any) -> MagicMock:
        seen["HF_HUB_OFFLINE"] = os.environ.get("HF_HUB_OFFLINE")
        return MagicMock()

    monkeypatch.setattr("fastembed.TextEmbedding", _capturing_text_embedding)

    EmbeddingModel()

    assert seen["HF_HUB_OFFLINE"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ


def test_preload_nvidia_cuda_libraries_is_a_noop_without_the_nvidia_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "nvidia", None)

    _preload_nvidia_cuda_libraries()  # must not raise


def _fake_nvidia_namespace_package(nvidia_dir: Path) -> types.ModuleType:
    """Build a fake `nvidia` module shaped like the real one: a PEP 420 namespace package with
    no `__file__` (only `__path__`) -- `_preload_nvidia_cuda_libraries` must use `__path__`,
    which is what broke in production the first time this used `__file__` instead (a namespace
    package's `__file__` is always `None`, so that version silently found nothing to preload)."""
    fake_nvidia = types.ModuleType("nvidia")
    fake_nvidia.__file__ = None
    fake_nvidia.__path__ = [str(nvidia_dir)]  # type: ignore[attr-defined]
    return fake_nvidia


def test_preload_nvidia_cuda_libraries_finds_libraries_via_dunder_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nvidia_dir = tmp_path / "nvidia"
    (nvidia_dir / "cu13" / "lib").mkdir(parents=True)
    (nvidia_dir / "cu13" / "lib" / "libfake.so.1").write_bytes(b"not a real shared library")
    monkeypatch.setitem(sys.modules, "nvidia", _fake_nvidia_namespace_package(nvidia_dir))
    calls: list[str] = []
    monkeypatch.setattr(
        embedding_module.ctypes, "CDLL",
        lambda path, mode: calls.append(path))  # type: ignore[misc]

    _preload_nvidia_cuda_libraries()

    assert calls == [str(nvidia_dir / "cu13" / "lib" / "libfake.so.1")]


def test_preload_nvidia_cuda_libraries_tolerates_libraries_that_fail_to_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    nvidia_dir = tmp_path / "nvidia"
    (nvidia_dir / "cu13" / "lib").mkdir(parents=True)
    (nvidia_dir / "cu13" / "lib" / "libfake.so.1").write_bytes(b"not a real shared library")
    monkeypatch.setitem(sys.modules, "nvidia", _fake_nvidia_namespace_package(nvidia_dir))

    _preload_nvidia_cuda_libraries()  # the bogus .so fails to load for real; must not raise
