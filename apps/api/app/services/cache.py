"""Content-addressed cache of the slow ingest steps: the same drawing or xref
package uploaded again (a repeated test run, a demo rehearsal) skips the DWG
conversion, the package merge and the parse — minutes become seconds.

A key is the sha256 of the input file plus a fingerprint of the code that
produces the result (the source of the modules involved) and of the
converter, so any code change invalidates the entries on its own. Entries
live in DATA_DIR/cache, capped at GREENPLAN_CACHE_MAX_GB (default 3); the
least recently used are evicted. GREENPLAN_CACHE=0 disables the cache.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Callable

from .. import config

log = logging.getLogger("greenplan.cache")


def enabled() -> bool:
    return os.environ.get("GREENPLAN_CACHE", "1") != "0"


def _max_bytes() -> float:
    return float(os.environ.get("GREENPLAN_CACHE_MAX_GB", "3")) * 2**30


def cache_dir() -> Path:
    return config.DATA_DIR / "cache"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_fingerprint(*modules: ModuleType) -> str:
    """Hash of the modules' source — changes whenever their code does."""
    h = hashlib.sha256()
    for m in modules:
        h.update(Path(m.__file__).read_bytes())
    return h.hexdigest()[:16]


def make_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def get(key: str) -> Path | None:
    """The entry directory, or None. A hit refreshes its age for eviction."""
    if not enabled():
        return None
    d = cache_dir() / key
    if not (d / ".complete").exists():
        return None
    now = time.time()
    os.utime(d, (now, now))
    return d


def put(key: str, fill: Callable[[Path], None]) -> Path | None:
    """Create the entry: `fill(tmp_dir)` writes its files; the directory is
    renamed into place only when complete. Failures never break the caller."""
    if not enabled():
        return None
    root = cache_dir()
    tmp = root / f".tmp_{uuid.uuid4().hex}"
    try:
        tmp.mkdir(parents=True)
        fill(tmp)
        (tmp / ".complete").write_text("ok")
        final = root / key
        if final.exists():
            shutil.rmtree(tmp, ignore_errors=True)
            return final
        tmp.rename(final)
        _evict(root)
        return final
    except Exception:  # noqa: BLE001 — a cache must never fail the upload
        log.warning("cache: could not store %s", key, exc_info=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def _size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def _evict(root: Path) -> None:
    entries = [(d.stat().st_mtime, _size(d), d) for d in root.iterdir()
               if d.is_dir() and not d.name.startswith(".tmp_")]
    total = sum(s for _, s, _ in entries)
    for _, size, d in sorted(entries):
        if total <= _max_bytes():
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size
