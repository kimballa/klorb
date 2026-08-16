# Pin `tree-sitter` core to exactly `0.25.0` for grammar ABI compatibility

* Date: 2026-08-15

## Question

`klorb.search_index.chunkers`' structural code chunkers depend on `tree-sitter` (the core Python
bindings) plus `tree-sitter-python`/`tree-sitter-typescript` (compiled grammar packages). With
`tree-sitter >= 0.26.0, < 1.0.0` (the version range initially chosen — the newest available at the
time) alongside `tree-sitter-python==0.25.0`/`tree-sitter-typescript==0.23.2`, chunking real files
from this repository crashed the interpreter outright (`Fatal Python error: Segmentation fault`,
reproducible via `-X faulthandler`) partway through a full-repo sweep — not on every file, and not
inside klorb's own code: the fault trace pointed into `tiktoken`'s `encode()`, called from
`Chunk.create()` on a chunk whose `Chunk.start_line`/`end_line` had visibly corrupted values (`0`,
`0`) for a node that, tested in isolation, parsed with correct coordinates. That shape — a crash in
unrelated, downstream code, working with visibly corrupted data, after many prior successful calls —
is a classic native heap-corruption symptom, not a bug reachable from pure Python. What's the fix?

## Answer

Pin `tree-sitter` to exactly `0.25.0` (`"tree-sitter == 0.25.0"` in `klorb/pyproject.toml`), not a
`>=`/`<` range. Verified fix: chunking every `.py` file in this repository (436 files, including the
specific file — `evals/cases.py` — that reproduced the crash) completes cleanly with `tree-sitter==
0.25.0` alongside the same `tree-sitter-python==0.25.0`/`tree-sitter-typescript==0.23.2`, and crashes
reliably with `tree-sitter==0.26.0` under the exact same grammar package versions. The grammar
packages weren't changed — only the core binding version.

## Reasoning

* **This is an ABI compatibility issue between the core bindings and the compiled grammar packages,
  not a bug in klorb's own chunking code.** A tree-sitter grammar package ships a compiled parser
  table generated against a specific tree-sitter ABI version; the core Python bindings and the
  grammar package must agree on that ABI's memory layout for node/tree structures. `tree-sitter-
  python==0.25.0`/`tree-sitter-typescript==0.23.2` predate `tree-sitter==0.26.0`'s release, and
  neither grammar package declares an upper bound on `tree-sitter` (both show `Requires:` empty via
  `pip show`), so `pip`/`uv` happily resolved the newest core version with no signal that it had
  silently drifted out of ABI sync with the grammars actually installed.
* **Pinning exactly, not a range,** follows the same reasoning as the existing `shfmt-py == 4.0.0`
  precedent (`klorb/pyproject.toml`, see `docs/plans/ready/004-bash-permissions-and-bash-tool.md`):
  a routine open-ended-range upgrade could silently reintroduce this exact crash with no accompanying
  code change to blame, since the failure mode is a native segfault far downstream of any Python
  stack trace that would obviously implicate a dependency bump. An exact pin forces a deliberate,
  re-verified decision (re-run the full-repo chunking sweep, per the "Reasoning" bullet above) before
  ever moving off `0.25.0`.
* **Only `tree-sitter` core needed pinning, not the grammar packages.** The reproduction isolates the
  incompatibility to the core/grammar ABI boundary specifically at `tree-sitter==0.26.0`; there's no
  evidence the grammar packages' own existing range constraints (`>= 0.25.0, < 1.0.0` /
  `>= 0.23.0, < 1.0.0`) are the unstable side of this pairing, so they're left as open ranges.

### Rejected alternatives

* **Constrain the field of possible chunks instead of fixing the dependency version** (e.g. limit the
  size or structure of what a "leftover statement run" chunk can be, on the theory that some specific
  input shape triggers the bug). Rejected once isolation testing showed the exact same node/text,
  parsed and chunked standalone, produced correct results — the corruption isn't about any particular
  input shape reaching klorb's chunker code, it's memory corruption originating in the native
  extension itself before klorb's code ever sees bad data.
* **Catch the segfault and degrade gracefully.** Not possible in principle: a genuine native
  segmentation fault terminates the process — there is no Python-level exception to catch, unlike an
  ordinary parse failure (which `TreeSitterChunker.chunk()` already handles by producing no
  structural chunks for that file).
