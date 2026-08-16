#!/usr/bin/env python3
# © Copyright 2026 Aaron Kimball
"""Maintainer-only script: fetches the local search-index embedding model from Hugging Face once
and writes it into `klorb.resources/embedding-model/` in the on-disk cache layout `fastembed`/
`huggingface_hub` read back offline, so the resulting files can be committed (via Git LFS) and
shipped as klorb package data. Not installed as a console script and never run by an end user or
at klorb runtime -- see docs/specs/local-search-index.md.

Usage: `venv/bin/python scripts/fetch_embedding_model.py`
"""

import shutil
import sys
from pathlib import Path

from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"
TARGET_DIR = Path(__file__).resolve().parent.parent / "src" / "klorb" / "resources" / "embedding-model"


def _dereference_snapshot_symlinks(target_dir: Path) -> None:
    """Replace every symlink under a `snapshots/*/` directory with a real copy of the blob it
    points to, then delete the now-unreferenced `blobs/` directories -- a wheel can't reliably
    ship symlinks (unsupported on Windows, and easily flattened/duplicated by the sdist/wheel
    build step), so the bundled tree must hold real files at the paths `huggingface_hub`'s
    offline cache resolution expects (`refs/main` plus `snapshots/<hash>/<filename>`) rather than
    the symlink-into-`blobs/` layout a live download produces.
    """
    for snapshot_file in target_dir.glob("*/snapshots/*/*"):
        if snapshot_file.is_symlink():
            real_target = snapshot_file.resolve()
            snapshot_file.unlink()
            shutil.copyfile(real_target, snapshot_file)
    for blobs_dir in target_dir.glob("*/blobs"):
        shutil.rmtree(blobs_dir)


def main() -> int:
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True)
    print(f"Fetching {MODEL_NAME!r} into {TARGET_DIR} ...")
    TextEmbedding(model_name=MODEL_NAME, cache_dir=str(TARGET_DIR))
    _dereference_snapshot_symlinks(TARGET_DIR)
    # Locks/version-check files are download-session bookkeeping, not part of the model itself --
    # dropping them keeps the bundled tree deterministic and avoids committing throwaway files.
    for junk_dir in TARGET_DIR.glob("**/.locks"):
        shutil.rmtree(junk_dir)
    version_file = TARGET_DIR / "version.txt"
    if version_file.exists():
        version_file.unlink()
    print(f"Done. Run `git lfs track` / `git add {TARGET_DIR}` to stage the fetched model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
