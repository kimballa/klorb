# GPU (CUDA) deps installed via `bin/install-cuda-nvidia.sh`, not a default `pyproject.toml` dependency; macOS uses Core ML instead

* Date: 2026-08-16

## Question

[00196](00196-gpu-embedding-uses-cuda-not-directml.md) made `onnxruntime-gpu[cuda,cudnn]` (plus
its `nvidia-cuda-runtime`/`nvidia-cublas`/`nvidia-cuda-nvrtc`/`nvidia-cudnn-cu13`/`nvidia-curand`
dependencies) a default `klorb/pyproject.toml` dependency on Linux/Windows, resolved via a
`sys_platform` marker and kept from conflicting with `fastembed`'s own plain-`onnxruntime`
dependency via a `uv pip compile/install --excludes` mechanism (`klorb/uv-excludes.txt`) applied
to every dependency-resolution Makefile target. This makes `klorb/release-requirements.txt` and
`klorb/dev-requirements.txt` platform-specific: whichever platform ran `make sync_deps` decides
which branch of the `sys_platform` marker resolves, baking that choice into the committed lock
files. Since `onnxruntime-gpu` ships no macOS wheel, a lock file generated on Linux/Windows is
literally uninstallable on macOS (`uv pip install -r release-requirements.txt` fails outright,
not just "runs without GPU"). How should GPU deps be structured instead, and what should macOS
do for GPU acceleration in the meantime?

## Answer

Move the CUDA/NVIDIA dependency set out of `pyproject.toml` entirely, into its own
`klorb/nvidia-requirements.in` compiled to `klorb/nvidia-requirements.txt` by `make sync_deps`
(a separate `uv pip compile` invocation, no `--excludes` needed since it's never mixed with the
base resolution). `bin/install-cuda-nvidia.sh` (repo root) installs it into `klorb/venv` on
demand: `uv pip uninstall onnxruntime` (clearing out `fastembed`'s plain-CPU dependency) then
`uv pip install -r nvidia-requirements.txt`. `klorb/pyproject.toml` goes back to depending on
nothing beyond `fastembed` for `onnxruntime`, exactly as before 00196 -- `release-requirements.txt`/
`dev-requirements.txt` are platform-agnostic again, and `klorb/Makefile`'s `venv`/`sync_deps`/
`install_deps`/`install_dev_deps` targets drop every `--excludes` flag (`klorb/uv-excludes.txt`
is deleted).

On macOS, GPU acceleration doesn't need any of this: `EmbeddingModel(use_gpu=True)` requests
`CoreMLExecutionProvider` there instead of CUDA, which ships in the default `onnxruntime` PyPI
wheel already (`embedding._gpu_provider_for_platform()` picks Core ML on `sys.platform ==
"darwin"`, CUDA on `"linux"`/`"win32"`). No install script needed on macOS -- `try_gpu_embedding_
model()`'s existing "attempt GPU, fall back to CPU silently" policy just works there out of the
box, same as everywhere else.

## Reasoning

* **A committed lock file has to install on every platform klorb supports, not just whichever
  platform happened to run `make sync_deps` last.** `sys_platform`-marker-based conditional
  dependencies are fine for a *live* resolve (`uv`/`pip` evaluate the marker against the machine
  actually running the install), but `uv pip compile` without `--universal` resolves for the
  *current* platform only and bakes that choice into the `.txt` file -- there's no way for a
  single non-universal lock file to simultaneously commit to "onnxruntime-gpu on Linux/Windows"
  and "plain onnxruntime on macOS." Splitting the GPU deps into their own never-conditionally-
  compiled file sidesteps the problem instead of fighting it.
* **This also deletes the `--excludes` mechanism's own failure mode.** 00196's fix required
  passing `--excludes klorb/uv-excludes.txt` to *every* `uv` invocation that resolves from
  `pyproject.toml` (`sync_deps`'s two compiles, `install_deps`, `install_dev_deps`, and the
  `venv` target's editable install) -- miss one and the conflict silently comes back on the next
  `make` invocation that hits it (this happened twice while building 00196). With GPU deps in
  their own file, there's nothing to exclude: `pyproject.toml` only ever resolves to plain
  `onnxruntime`, and the only place `onnxruntime-gpu` gets installed is the one explicit,
  deliberate `bin/install-cuda-nvidia.sh` run.
* **An explicit script over an opt-in pip extra (`pip install klorb[gpu]`).** klorb isn't
  installed by end users via `pip install klorb` -- `make install_deps`/`install_dev_deps`
  install from committed, `uv`-compiled lock files, not a live resolve of `pyproject.toml`'s
  extras. A `[gpu]` extras group would need its own separate lock file compiled the same way
  `nvidia-requirements.in` already is, for no benefit over a plain requirements file plus a
  script that also handles the `onnxruntime` uninstall step (see next point) and prints a clear
  "not applicable on macOS" message.
* **The script explicitly uninstalls plain `onnxruntime` first, rather than relying on install
  order.** `onnxruntime` and `onnxruntime-gpu` both write to `site-packages/onnxruntime/`, and
  (per 00196) `pip`/`uv` silently let the second install's files win with no warning. An
  explicit `uv pip uninstall onnxruntime` before installing from `nvidia-requirements.txt` makes
  the script's outcome deterministic regardless of what was already installed, rather than
  depending on undocumented last-writer-wins file-overwrite behavior.
* **Core ML needs none of CUDA's packaging machinery.** It's an Apple system framework, already
  linked into the default macOS `onnxruntime` wheel -- no separate GPU package, no `nvidia-*`
  runtime libraries, no `ctypes.CDLL` preload (`_preload_nvidia_cuda_libraries()` stays CUDA-
  only; `EmbeddingModel.__init__` only calls it when `_gpu_provider_for_platform()` picked CUDA).
  `EmbeddingModel`'s own post-construction check (does the constructed session's
  `get_providers()` actually include the requested provider) already generalized cleanly to
  cover both: `fastembed`'s own CUDA-specific fallback warning turned out not to fire for Core
  ML at all (its "attempt to set X failed" check is hardcoded to
  `"CUDAExecutionProvider"` in fastembed's own source), so the check was rewritten to inspect
  the real session directly (`self._model.model.model.get_providers()`, `# type: ignore[attr-
  defined]` -- no public fastembed API exposes this) instead of relying on that provider-
  specific warning.

### Rejected alternatives

* **`uv pip compile --universal`** to produce one lock file valid across platforms. Rejected:
  klorb's Makefile targets don't currently pass `--universal`, and adopting it project-wide is a
  larger change than this decision needs -- the separate-file split solves the specific GPU-deps
  problem without touching how the rest of the dependency set is resolved.
* **Keeping `onnxruntime-gpu` as a default dependency but gating the `sys_platform` marker
  differently** (e.g. `!= 'darwin'` instead of enumerating `linux`/`win32`). Doesn't help --
  the underlying problem is that a *non-universal* compile can only ever commit to one branch of
  any marker, not which marker expression is used.
