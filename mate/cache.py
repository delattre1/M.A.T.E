"""File-backed JSON cache, so a re-plan costs the open-data servers nothing.

A corrupt or half-written entry is a miss, never an exception: the worst case of
a bad cache file must be one extra request, not a dead planner.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .settings import Settings

FOREVER = -1.0  # ttl_hours sentinel: coordinates of a town do not go stale


def cache_key(source: str, query: Any) -> str:
    blob = json.dumps([source, query], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


class Cache:
    def __init__(self, settings: Settings) -> None:
        self.dir = Path(settings.cache_dir)
        self.ttl_hours = settings.cache_ttl_hours

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text("utf-8"))
            stored_at = float(entry["at"])
            ttl = float(entry["ttl_h"])
            value = entry["v"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if ttl != FOREVER and time.time() - stored_at > ttl * 3600:
            return None
        return value

    def put(self, key: str, value: Any, ttl_hours: float | None = None) -> None:
        entry = {
            "at": time.time(),
            "ttl_h": self.ttl_hours if ttl_hours is None else ttl_hours,
            "v": value,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        written = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False)
            os.replace(tmp, self._path(key))
            written = True
        except OSError:
            pass  # a disk that will not take the cache must not take the trip down
        finally:
            if not written:
                Path(tmp).unlink(missing_ok=True)

    def cached(
        self,
        source: str,
        key: Any,
        fn: Callable[[], Any],
        ttl_hours: float | None = None,
    ) -> Any:
        k = cache_key(source, key)
        hit = self.get(k)
        if hit is not None:
            return hit
        value = fn()
        self.put(k, value, ttl_hours)
        return value
