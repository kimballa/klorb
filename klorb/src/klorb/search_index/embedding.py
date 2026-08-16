# © Copyright 2026 Aaron Kimball
"""`EmbeddingModel`: local ONNX text embedding via `fastembed`, loaded from the bundled model
installed to `embedding_model_target_dir()` by `install_embedding_model()` -- never downloaded at
runtime. See docs/specs/local-search-index.md.
"""

import ctypes
import importlib.resources
import logging
import os
import shutil
import warnings
from pathlib import Path

import numpy as np

from klorb.paths import get_klorb_data_dir

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384
EMBEDDING_THREADS = 2
"""Caps `onnxruntime`'s intra-op thread pool for the embedding session. Unset (the `fastembed`
default), it sizes the pool to the machine's full physical core count."""
EMBEDDING_MODEL_RESOURCE_NAME = "embedding-model"
"""Directory name of the packaged embedding-model resource tree within `klorb.resources`, and
the subdirectory of `KLORB_DATA_DIR` it's copied to -- the on-disk cache layout
`huggingface_hub`/`fastembed` read back offline (see `install_embedding_model`)."""

CUDA_PROVIDER = "CUDAExecutionProvider"
"""`onnxruntime` execution provider name for NVIDIA CUDA GPU acceleration -- see
docs/adrs/00196-gpu-embedding-uses-cuda-not-directml.md for why CUDA over DirectML (whose
`onnxruntime-directml` PyPI package ships Windows-only wheels, unusable from WSL2's own Linux
Python)."""

_HF_HUB_OFFLINE_ENV_VAR = "HF_HUB_OFFLINE"


def embedding_model_target_dir() -> Path:
    """Where `install_embedding_model()` copies the packaged model tree to, and where
    `EmbeddingModel` points `fastembed`'s `cache_dir` at: `$KLORB_DATA_DIR/embedding-model`."""
    return get_klorb_data_dir() / EMBEDDING_MODEL_RESOURCE_NAME


def embedding_model_available() -> bool:
    """Whether `embedding_model_target_dir()` exists -- checked up front by
    `klorb.search_index.indexer.WorkspaceIndexer` before starting any background work, so an
    environment that never ran `klorb init` (most unit tests, a fresh CI container) doesn't spawn
    a thread doomed to fail on its first `EmbeddingModel()` construction."""
    return embedding_model_target_dir().is_dir()


def cuda_available() -> bool:
    """Whether the installed `onnxruntime` build was compiled with `CUDAExecutionProvider` --
    true only if `onnxruntime-gpu` (not the plain CPU `onnxruntime` package klorb otherwise
    depends on) is installed; both provide the same importable `onnxruntime` module, so this is
    a build-capability check, not proof the provider can actually load at runtime (its own CUDA/
    cuDNN shared libraries might still be missing -- see `EmbeddingModel`'s own post-construction
    check for that). Neither `onnxruntime-gpu` nor the `nvidia-*` runtime packages it needs are
    declared klorb dependencies (see the ADR referenced by `CUDA_PROVIDER`) -- a user who wants
    `--gpu` installs them manually."""
    import onnxruntime
    return CUDA_PROVIDER in onnxruntime.get_available_providers()


def _preload_nvidia_cuda_libraries() -> None:
    """Best-effort `ctypes.CDLL(..., mode=RTLD_GLOBAL)` preload of every shared library shipped
    by the `nvidia-*` pip packages (`nvidia-cublas`, `nvidia-cudnn-cu13`, `nvidia-curand`, ...)
    onnxruntime's CUDA execution provider dynamically links against.

    Those packages install their `.so` files under `site-packages/nvidia/**/lib/`, a location
    `onnxruntime`'s own `dlopen()` calls don't search -- and setting `LD_LIBRARY_PATH` from
    within an already-running process doesn't help either, since the dynamic loader only
    consults it once at process start, not per `dlopen()` call. Explicitly preloading each
    library (in no particular order; the loader resolves cross-library symbol references lazily
    against whatever's already loaded, so load order doesn't matter here) with `RTLD_GLOBAL`
    makes them available for onnxruntime's own subsequent `dlopen()` of
    `libonnxruntime_providers_cuda.so` to resolve against, without requiring the caller to set
    `LD_LIBRARY_PATH` before starting the process at all. `nvidia` is a namespace package (PEP
    420, no `__init__.py`) -- it has no `__file__`, so its installed location comes from
    `__path__` instead, which can list more than one directory if several `nvidia-*`
    distributions each contributed their own. A no-op if the `nvidia` package isn't installed (no
    CUDA runtime packages present) or a given library fails to load (surfaced later as a
    provider-loading failure `EmbeddingModel` itself checks for)."""
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
    """Recursively copy the packaged `embedding-model` resource tree (`klorb.resources/
    embedding-model/`, fetched once via `scripts/fetch_embedding_model.py` and shipped as package
    data) into `embedding_model_target_dir()`, creating it as needed. Always copies, the same as
    `klorb.token_estimate.install_tiktoken_cache()` and for the same reason: this is package data
    the running klorb version ships with, not something a user hand-edits. Raises `OSError` if
    the target can't be created or written."""
    target = embedding_model_target_dir()
    with importlib.resources.as_file(
        importlib.resources.files("klorb.resources").joinpath(EMBEDDING_MODEL_RESOURCE_NAME)
    ) as source:
        shutil.copytree(source, target, dirs_exist_ok=True)
    return [f"Copied embedding model to {target}."]


class EmbeddingModel:
    """Wraps a `fastembed.TextEmbedding` loaded from the bundled `EMBEDDING_MODEL_NAME` model,
    forcing `HF_HUB_OFFLINE` so a missing/stale local copy fails fast rather than silently
    reaching Hugging Face. `threads` caps the wrapped session's own intra-op thread pool,
    defaulting to `EMBEDDING_THREADS`; a caller running several instances concurrently (see
    `WorkspaceIndexer.run_foreground_scan`) passes a smaller value per instance so their combined
    CPU usage stays predictable. `use_gpu` requests `CUDAExecutionProvider` (falling back to
    `CPUExecutionProvider` only for ops CUDA doesn't implement) instead of CPU-only execution --
    raises `RuntimeError` if `cuda_available()` is false, or if the provider is compiled in but
    still fails to actually load (its CUDA/cuDNN shared libraries missing or mismatched), since
    silently running on CPU after a caller explicitly asked for GPU would be confusing.
    Construction raises `FileNotFoundError` if `embedding_model_target_dir()` doesn't exist yet
    (`klorb init` hasn't run) -- the caller is expected to treat that as "the workspace index
    feature is unavailable", not to fall back to a network download.
    """

    def __init__(self, *, threads: int = EMBEDDING_THREADS, use_gpu: bool = False) -> None:
        target_dir = embedding_model_target_dir()
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"Embedding model not found at {target_dir}; run `klorb init` first.")
        providers: list[str] | None = None
        if use_gpu:
            if not cuda_available():
                raise RuntimeError(
                    "GPU embedding requested but the installed onnxruntime build has no "
                    f"{CUDA_PROVIDER}. Install onnxruntime-gpu (and matching nvidia-cuda-runtime/"
                    "nvidia-cublas/nvidia-cuda-nvrtc/nvidia-cudnn/nvidia-curand packages for your "
                    "CUDA version) to use --gpu.")
            _preload_nvidia_cuda_libraries()
            providers = [CUDA_PROVIDER, "CPUExecutionProvider"]
        previous_offline = os.environ.get(_HF_HUB_OFFLINE_ENV_VAR)
        os.environ[_HF_HUB_OFFLINE_ENV_VAR] = "1"
        try:
            # Imported lazily so a process that never touches the search index never pays
            # onnxruntime's import cost.
            from fastembed import TextEmbedding

            # fastembed only *warns* (doesn't raise) if CUDAExecutionProvider is requested but
            # fails to actually load -- caught here and turned into a hard failure, since a
            # caller who explicitly asked for GPU should never silently end up on CPU instead.
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self._model = TextEmbedding(
                    model_name=EMBEDDING_MODEL_NAME, cache_dir=str(target_dir), threads=threads,
                    providers=providers)
        finally:
            if previous_offline is None:
                del os.environ[_HF_HUB_OFFLINE_ENV_VAR]
            else:
                os.environ[_HF_HUB_OFFLINE_ENV_VAR] = previous_offline
        if use_gpu and any(CUDA_PROVIDER in str(warning.message) for warning in caught):
            raise RuntimeError(
                f"GPU embedding requested and {CUDA_PROVIDER} is compiled into onnxruntime, but "
                "it failed to actually load -- check that matching CUDA runtime/cuDNN libraries "
                "are installed for your onnxruntime-gpu version.")
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


_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    """Return the shared `EmbeddingModel`, caching on first use.
    (Loading the ONNX model is expensive.)"""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model
