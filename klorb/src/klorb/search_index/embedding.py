# © Copyright 2026 Aaron Kimball
"""`EmbeddingModel`: local ONNX text embedding via `fastembed`, loaded from the bundled model
installed to `embedding_model_target_dir()` by `install_embedding_model()`.
"""

import ctypes
import importlib.resources
import logging
import os
import shutil
import sys
from pathlib import Path

import numpy as np

from klorb.paths import get_klorb_data_dir

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384
EMBEDDING_THREADS = 2
"""Caps `onnxruntime`'s intra-op thread pool for the embedding session."""
MAX_GPU_INDEXING_THREADS = 4
"""Default cap on `klorb index scan`'s worker-thread count when GPU embedding is in use, since a
shared CUDA `EmbeddingModel` session and the search index's single-writer SQLite connection both
serialize concurrent callers.
"""
EMBEDDING_MODEL_RESOURCE_NAME = "embedding-model"
"""Directory name of the packaged embedding-model resource tree within `klorb.resources`, and
the subdirectory of `KLORB_DATA_DIR` it's copied to.
"""

CUDA_PROVIDER = "CUDAExecutionProvider"
"""`onnxruntime` execution provider name for NVIDIA CUDA GPU acceleration."""

COREML_PROVIDER = "CoreMLExecutionProvider"
"""`onnxruntime` execution provider name for Apple's Core ML."""

_HF_HUB_OFFLINE_ENV_VAR = "HF_HUB_OFFLINE"


def embedding_model_target_dir() -> Path:
    """Where `install_embedding_model()` copies the packaged model tree to."""
    return get_klorb_data_dir() / EMBEDDING_MODEL_RESOURCE_NAME


def embedding_model_available() -> bool:
    """Whether `embedding_model_target_dir()` exists."""
    return embedding_model_target_dir().is_dir()


def cuda_available() -> bool:
    """Whether the installed `onnxruntime` build was compiled with `CUDAExecutionProvider`."""
    import onnxruntime
    return CUDA_PROVIDER in onnxruntime.get_available_providers()


def coreml_available() -> bool:
    """Whether the installed `onnxruntime` build was compiled with `CoreMLExecutionProvider`."""
    import onnxruntime
    return COREML_PROVIDER in onnxruntime.get_available_providers()


def _gpu_provider_for_platform() -> str | None:
    """Which `onnxruntime` execution provider `EmbeddingModel(use_gpu=True)` should request on
    this platform."""
    if sys.platform == "darwin":
        return COREML_PROVIDER
    if sys.platform in ("linux", "win32"):
        return CUDA_PROVIDER
    return None


def _preload_nvidia_cuda_libraries() -> None:
    """Best-effort `ctypes.CDLL(..., mode=RTLD_GLOBAL)` preload of every shared library shipped
    by the `nvidia-*` pip packages onnxruntime's CUDA execution provider dynamically links against.

    Those packages install their `.so` files under `site-packages/nvidia/**/lib/`, a location
    `onnxruntime`'s own `dlopen()` calls don't search. Explicitly preloading each library with
    `RTLD_GLOBAL` makes them available for onnxruntime's own subsequent `dlopen()` of
    `libonnxruntime_providers_cuda.so` to resolve against.
    """
    try:
        import nvidia
    except ImportError:
        return
    lib_paths: list[Path] = []
    for nvidia_dir in nvidia.__path__:
        lib_paths.extend(Path(nvidia_dir).glob("**/*.so*"))
    for lib_path in sorted(lib_paths):
        try:
            ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
        except OSError:
            logger.debug("Failed to preload %s.", lib_path, exc_info=True)


def install_embedding_model() -> list[str]:
    """Recursively copy the packaged `embedding-model` resource tree into
    `embedding_model_target_dir()`, creating it as needed. Always copies rather than checking
    whether the destination already matches. Raises `OSError` if the target can't be created or
    written."""
    target = embedding_model_target_dir()
    with importlib.resources.as_file(
        importlib.resources.files("klorb.resources").joinpath(EMBEDDING_MODEL_RESOURCE_NAME)
    ) as source:
        shutil.copytree(source, target, dirs_exist_ok=True)
    return [f"Copied embedding model to {target}."]


class EmbeddingModel:
    """Wraps a `fastembed.TextEmbedding` loaded from the bundled `EMBEDDING_MODEL_NAME` model,
    forcing `HF_HUB_OFFLINE` so a missing/stale local copy fails fast rather than silently
    reaching Hugging Face.

    * `threads` caps the wrapped session's own intra-op thread pool, defaulting to
      `EMBEDDING_THREADS`.
    * `use_gpu` requests the platform's GPU provider. Raises
      `RuntimeError` if this platform has no GPU story, the provider isn't compiled into the
      installed `onnxruntime`, or it's compiled in but still fails to actually load.

    Construction raises `FileNotFoundError` if `embedding_model_target_dir()` doesn't exist yet.
    """

    def __init__(self, *, threads: int = EMBEDDING_THREADS, use_gpu: bool = False) -> None:
        target_dir = embedding_model_target_dir()
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"Embedding model not found at {target_dir}; run `klorb init` first.")
        providers: list[str] | None = None
        gpu_provider: str | None = None
        if use_gpu:
            gpu_provider = _gpu_provider_for_platform()
            if gpu_provider is None:
                raise RuntimeError(f"GPU embedding isn't supported on {sys.platform!r}.")
            import onnxruntime
            if gpu_provider not in onnxruntime.get_available_providers():
                hint = (
                    " Run ./bin/install-cuda-nvidia.sh to install onnxruntime-gpu and matching "
                    "NVIDIA CUDA/cuDNN runtime packages." if gpu_provider == CUDA_PROVIDER else "")
                raise RuntimeError(
                    "GPU embedding requested but the installed onnxruntime build has no "
                    f"{gpu_provider}.{hint}")
            if gpu_provider == CUDA_PROVIDER:
                _preload_nvidia_cuda_libraries()
            providers = [gpu_provider, "CPUExecutionProvider"]
        previous_offline = os.environ.get(_HF_HUB_OFFLINE_ENV_VAR)
        os.environ[_HF_HUB_OFFLINE_ENV_VAR] = "1"
        try:
            # Imported lazily so a process that never touches the search index never pays
            # onnxruntime's import cost.
            from fastembed import TextEmbedding
            self._model = TextEmbedding(
                model_name=EMBEDDING_MODEL_NAME, cache_dir=str(target_dir), threads=threads,
                providers=providers,
                # fastembed's own `cuda` parameter defaults to Device.AUTO, which auto-detects
                # and tries CUDAExecutionProvider whenever it's compiled into the installed
                # onnxruntime -- regardless of `providers` here. False always, since this class
                # already decides GPU-vs-CPU explicitly itself.
                cuda=False)
        finally:
            if previous_offline is None:
                del os.environ[_HF_HUB_OFFLINE_ENV_VAR]
            else:
                os.environ[_HF_HUB_OFFLINE_ENV_VAR] = previous_offline
        if gpu_provider is not None:
            # The actual providers the constructed session ended up using -- reached via
            # fastembed's own model wrapper.
            actual_providers = self._model.model.model.get_providers()  # type: ignore[attr-defined]
            if gpu_provider not in actual_providers:
                raise RuntimeError(
                    f"GPU embedding requested and {gpu_provider} is compiled into onnxruntime, "
                    f"but it failed to actually load (got {actual_providers}).")
        logger.debug(
            "Loaded embedding model %r from %s (providers=%s).",
            EMBEDDING_MODEL_NAME, target_dir, providers or ["CPUExecutionProvider"])

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        """Embed `texts` as documents/passages (the index side of retrieval)."""
        return list(self._model.passage_embed(texts))

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single search query, using the model's query-side (possibly asymmetric)
        embedding path."""
        return next(iter(self._model.query_embed([text])))


def try_gpu_embedding_model(*, threads: int = EMBEDDING_THREADS) -> EmbeddingModel | None:
    """Attempt to build a GPU (CUDA) `EmbeddingModel`, returning `None` instead of raising if CUDA
    isn't available or fails to actually load. Callers can then attempt a CPU-based construction."""
    if not cuda_available():
        return None
    try:
        return EmbeddingModel(threads=threads, use_gpu=True)
    except RuntimeError:
        logger.debug("GPU embedding unavailable; falling back to CPU.", exc_info=True)
        return None


_embedding_model: EmbeddingModel | None = None


def get_embedding_model(*, use_gpu: bool = True) -> EmbeddingModel:
    """Return the shared `EmbeddingModel`, caching on first use (loading the ONNX model is
    expensive). Prefers GPU (`try_gpu_embedding_model()`) over CPU when `use_gpu` is true; the
    cached model's provider is fixed by whichever `use_gpu` value first constructed it."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = (try_gpu_embedding_model() if use_gpu else None) or EmbeddingModel()
    return _embedding_model
