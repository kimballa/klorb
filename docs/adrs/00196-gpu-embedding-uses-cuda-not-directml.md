# GPU embedding uses CUDA, not DirectML, and is on by default via `onnxruntime-gpu`

> **Superseded in part** by
> [00197-gpu-nvidia-deps-installed-via-script-not-pyproject-default.md](00197-gpu-nvidia-deps-installed-via-script-not-pyproject-default.md):
> `onnxruntime-gpu` is no longer a default `pyproject.toml` dependency (it made
> `release-requirements.txt`/`dev-requirements.txt` uninstallable on macOS) -- it's installed
> on demand by `bin/install-cuda-nvidia.sh` instead. The CUDA-over-DirectML decision, the
> library-preload mechanism, the GPU-is-a-shared-singleton design, and the auto-fallback
> behavior below are all still current.

* Date: 2026-08-16

## Question

`klorb index scan`'s embedding step is CPU-bound and dominates scan wall time (confirmed via
`py-spy`: every worker thread samples inside `onnxruntime`'s `Run()`, not blocked on any lock).
The development machine has an NVIDIA RTX 3070 reachable from WSL2 (`nvidia-smi` works). What GPU
execution provider should `klorb.search_index.embedding.EmbeddingModel` use, and should it be
on by default?

## Answer

CUDA (`onnxruntime-gpu`'s `CUDAExecutionProvider`), on by default, with silent fallback to CPU
when GPU isn't available or fails to load. `klorb index scan` gets a `--no-gpu` flag to force
CPU; the background `WorkspaceIndexer` and `hybrid_search`'s query embedding pick up GPU
automatically too, through the same shared-singleton path (`get_embedding_model()`).

`onnxruntime-gpu[cuda,cudnn]` (pulling in the matching `nvidia-cuda-runtime`/`nvidia-cublas`/
`nvidia-cuda-nvrtc`/`nvidia-cudnn-cu13`/`nvidia-curand` packages) is a default `klorb/
pyproject.toml` dependency on Linux/Windows (`sys_platform == 'linux' or sys_platform ==
'win32'`); macOS keeps plain CPU-only `onnxruntime` (via `fastembed`'s own dependency), since
`onnxruntime-gpu` ships no macOS wheel at all.

Measured end-to-end on the dev machine (AMD Ryzen 7 9800X3D, 8 physical cores/16 threads, RTX
3070): a 948-file rebuild scan went from 158.2s (`-j 8`, GPU) versus ~630s (`-j 8`, CPU-only,
already tuned per-thread models) -- roughly 4x.

## Reasoning

* **DirectML was the first choice and doesn't work here.** WSL2 exposes the GPU through
  `/dev/dxg` + the Windows host's D3D12 driver, and `onnxruntime-directml` is ONNX Runtime's
  provider for exactly that path. It's a dead end anyway: `onnxruntime-directml`'s PyPI wheels
  are `win_amd64`-only (verified via the PyPI JSON API) -- there is no Linux wheel, so it can
  never be installed into WSL2's own Linux Python, regardless of the GPU being reachable at the
  driver level. CUDA is therefore the only GPU path actually usable from this environment.
* **CUDA needed a specific, non-obvious version combination.** The installed `onnxruntime-gpu`
  build (1.28.0) requires CUDA *13* and cuDNN 9, matching the driver's own max-supported CUDA
  version (`nvidia-smi` reported "CUDA Version: 13.1") -- not CUDA 12, which is what most current
  `pip install onnxruntime-gpu` guidance still assumes. NVIDIA's CUDA-13-era pip packages also
  renamed themselves: no more `-cu13` suffix (e.g. `nvidia-cublas`, not `nvidia-cublas-cu13`;
  the `-cu13`-suffixed variants that do exist, like `nvidia-cuda-runtime-cu13`, are PyPI
  placeholders marked "DEPRECATED: use nvidia-cuda-runtime instead"). Getting a working set
  required matching `onnxruntime-gpu`'s `ldd`-reported missing libraries one at a time
  (`libcublasLt.so.13`, then `libcurand.so.10`) against the new unsuffixed package names.
* **The `nvidia-*` packages' shared libraries aren't on any path `onnxruntime` searches by
  default.** They install under `site-packages/nvidia/**/lib/`. Setting `LD_LIBRARY_PATH` from
  within an already-running Python process doesn't help -- verified empirically: the dynamic
  loader only consults `LD_LIBRARY_PATH` once at process start, not per `dlopen()` call, so an
  in-process `os.environ["LD_LIBRARY_PATH"] = ...` before constructing the `InferenceSession` had
  no effect. The fix (`embedding._preload_nvidia_cuda_libraries`) is to `ctypes.CDLL(...,
  mode=RTLD_GLOBAL)`-preload every `.so` under the installed `nvidia` package's `__path__` before
  onnxruntime ever tries to load its own CUDA provider -- this **does** work regardless of when
  it runs in the process. `nvidia` is a PEP 420 namespace package (no `__init__.py`), so its
  install location comes from `__path__`, not `__file__` (`__file__` is always `None` for a
  namespace package) -- the first version of this preload function used `__file__` and silently
  found nothing to preload, passing every unit test (mocked) while failing in the real
  environment; see `test_preload_nvidia_cuda_libraries_finds_libraries_via_dunder_path`, a
  regression test for exactly this.
* **`onnxruntime`/`onnxruntime-gpu` conflict silently, not loudly, when both installed.** Both
  packages install files to the same `site-packages/onnxruntime/` path. Verified empirically:
  `pip install onnxruntime` on top of an existing `onnxruntime-gpu` install succeeds with no
  warning, and silently overwrites the GPU build's files -- `get_available_providers()`
  afterward showed CPU-only, with no error anywhere. Since `fastembed` (klorb's existing
  dependency) unconditionally requires plain `onnxruntime`, simply adding `onnxruntime-gpu`
  alongside it would race this exact conflict on every fresh install, with the outcome
  depending on installation order. The fix: `klorb/uv-excludes.txt` (`onnxruntime ; sys_platform
  == "linux" or sys_platform == "win32"`), passed via `uv pip compile --excludes` /
  `uv pip install --excludes` to every dependency-resolution step in `klorb/Makefile` (`sync_deps`,
  `install_deps`, `install_dev_deps`, and the editable `pip install -e .` in the `venv` target)
  -- `uv` drops `onnxruntime` from resolution entirely on those platforms rather than trying to
  satisfy it, so `onnxruntime-gpu` is the only thing that ever provides the `onnxruntime` import.
  Missing `--excludes` on even one of those Makefile targets reintroduces the conflict on the
  next `make` invocation that hits it -- this was hit twice while wiring it up, once for the
  `install_deps`/`install_dev_deps` targets and once for the `venv` target's own editable
  install, since each is a separate `uv` invocation with its own flag.
* **GPU is a shared singleton, not one instance per thread.** CPU scanning gives each worker
  thread its own `EmbeddingModel(threads=1)` (see `_scan_dirty_files`'s own docstring) to avoid
  contending on one thread-capped session. A GPU has no equivalent per-thread win -- it's one
  physical device -- so `use_gpu=True` builds exactly one `EmbeddingModel` up front
  (`try_gpu_embedding_model()`) and shares it across every worker thread regardless of
  `-j`/`num_threads`.
* **Auto-fallback, not a hard requirement.** Most environments -- CI, other developers' machines,
  anyone without an NVIDIA GPU, all of macOS -- won't have a working CUDA provider, and that's
  the ordinary case, not an error. `EmbeddingModel(use_gpu=True)` itself still raises `RuntimeError`
  if CUDA isn't available or fails to actually load (a useful low-level primitive, and what
  `--no-gpu`'s absence would look like if forced), but `try_gpu_embedding_model()` catches that and
  returns `None`, and every actual call site (`get_embedding_model()`, `_scan_dirty_files`) treats
  `None` as "fall back to CPU," never as an error to surface to the user.
* **`fastembed`'s own CUDA-fallback signal is a warning, not an exception.** Passing
  `providers=[CUDAExecutionProvider, CPUExecutionProvider]` and having the CUDA provider fail to
  actually load (e.g. missing shared libraries) doesn't raise from `fastembed.TextEmbedding()`'s
  constructor -- it emits a `RuntimeWarning` and silently continues on CPU. `EmbeddingModel`
  catches that warning itself (`warnings.catch_warnings(record=True)`) and converts it to a
  raised `RuntimeError`, since a caller that explicitly requested GPU (`use_gpu=True`) should
  never silently end up on CPU without at least a hard signal at that layer -- it's
  `try_gpu_embedding_model()`'s job, one layer up, to decide whether that signal is fatal or not.

### Rejected alternatives

* **DirectML.** No Linux wheel exists; see above.
* **Making `onnxruntime-gpu` an opt-in extra the user installs manually
  (`pip install klorb[gpu]`) instead of a default dependency.** Rejected once GPU was confirmed
  working and given a ~4x measured speedup with no downside on non-GPU platforms (CPU-only
  fallback is silent and automatic) -- the friction of a manual install step outweighs the
  ~1.7GB of additional download on Linux/Windows, where it's the overwhelmingly common target.
* **Letting the `onnxruntime`/`onnxruntime-gpu` conflict resolve by install order** (e.g.
  declaring `onnxruntime-gpu` after `fastembed` in `pyproject.toml` and hoping it installs
  second). Rejected: dependency resolution order isn't a stable, documented contract of `pip`/
  `uv`, and the failure mode (silent fallback to CPU, no error) is exactly the kind of thing that
  would go unnoticed until someone profiles a mysteriously slow scan.
