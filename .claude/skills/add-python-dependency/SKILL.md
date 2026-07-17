---
name: add-python-dependency
description: Add (or change the version constraint of) a Python package dependency for the klorb project. Use whenever a new import needs a third-party package that isn't already a dependency, or when bumping/loosening a version constraint. Covers runtime deps, dev-only deps, and type-stub packages, and the Makefile workflow that keeps the requirements lock files in sync.
---

# Adding a Python dependency to klorb

klorb's dependencies are declared in **one** place — `klorb/pyproject.toml` — and the
pinned `klorb/release-requirements.txt` / `klorb/dev-requirements.txt` lock files are
**generated** from it. Never hand-edit the lock files, and never run `pip`/`uv` directly:
both bypass the Makefile workflow that keeps the three files consistent.

## 1. Declare the dependency in `klorb/pyproject.toml`

Add the package name and its version constraint to the right list — and nothing else. Do
**not** write an explanatory comment next to the entry; the dependency list stays a plain
list of `name >= x, < y` lines.

* A **runtime** dependency (imported by `klorb/src/klorb/…`) goes in `[project]`'s
  `dependencies` array.
* A **development-only** dependency (linters, test tooling, and type-stub packages like
  `types-PyYAML`) goes in `[project.optional-dependencies]`'s `dev` array.
* Use the same constraint style as the neighbours: a lower bound and an exclusive upper
  bound on the next major version, e.g. `"PyYAML >= 6.0, < 7.0"`. Pin exactly (`== x.y.z`)
  only when there's a specific reason to, and if so, record that reason in an ADR under
  `docs/adrs/` rather than a comment (see the `shfmt-py == 4.0.0` precedent, whose reason
  lives in a plan doc, not an inline comment).
* If the package ships no type hints, add its stub package (`types-<name>`) to the `dev`
  array too, so `make typecheck` doesn't fail under `--disallow-untyped-calls`.

## 2. Cascade to the lock files with `make sync_deps`

From the repo root:

```
make -C klorb sync_deps
```

This runs `uv pip compile` for both the runtime and dev dependency sets and rewrites
`klorb/release-requirements.txt` and `klorb/dev-requirements.txt` accurately (resolving the
full transitive closure). Let it edit those files — don't touch them yourself. Review the
resulting diff: it should add your package (and any new transitive deps) and may also bump
unrelated pins, since the compile step resolves everything fresh.

## 3. Install into the venv with `make install_dev_deps`

Still from the repo root:

```
make -C klorb install_dev_deps
```

This installs the freshly-resolved dev lock file into `klorb/venv`, so the new package is
importable when you run the code and the test suite.

## 4. Verify

Run the standard CI loop and confirm it's clean:

```
make -C klorb lint typecheck test
```

## Summary

1. Edit `klorb/pyproject.toml` — package name + version constraint, **no comment**.
2. `make -C klorb sync_deps` — regenerates both lock files.
3. `make -C klorb install_dev_deps` — installs into the venv.
4. `make -C klorb lint typecheck test` — verify.

Never edit `release-requirements.txt` / `dev-requirements.txt` by hand, and never invoke
`pip`/`uv` directly — always go through the Makefile targets above.
