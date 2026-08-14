"""Shared HTTP session and a small TTL cache, used by every provider.

Everything here is deliberately synchronous: upstream endpoints are hit at
most a few times per hour thanks to the cache, and FastAPI runs sync route
helpers in a threadpool. This module has no opinion about which upstream it
talks to -- IMGW, Open-Meteo, whatever comes next -- that belongs in the
provider modules.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": settings.user_agent})
        return _session


class TTLCache:
    """Cache that also keeps the last good value as a fallback.

    If an upstream is briefly unreachable we would rather show slightly stale
    data than an error page, so ``get_or_fetch`` falls back to the expired
    entry when the refresh raises.

    One refresh per key runs at a time. Without that, the moment an entry
    expires every request that happens to be in flight fetches the same file
    at once -- a hundred people opening the page in the same second turned
    into a hundred identical downloads. Callers that already hold a stale
    value get it back immediately rather than queueing behind the refresh;
    only a cold key waits.

    ``max_entries`` bounds the store for caches of large values (decoded
    model fields are megabytes each and are keyed per run and forecast hour,
    so an unbounded store grows until the container is killed).
    """

    def __init__(self, max_entries: Optional[int] = None) -> None:
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._refreshing: Dict[str, threading.Lock] = {}
        self._max_entries = max_entries

    def get_or_fetch(self, key: str, ttl: int, fetch: Callable[[], Any]) -> Any:
        entry = self._get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]

        refresh = self._refresh_lock(key)
        if entry is not None and not refresh.acquire(blocking=False):
            return entry[1]  # someone else is already fetching it
        if entry is None:
            refresh.acquire()

        try:
            # Whoever waited for the lock may have been given what they wanted.
            entry = self._get(key)
            now = time.time()
            if entry and now - entry[0] < ttl:
                return entry[1]

            try:
                value = fetch()
            except Exception as exc:  # noqa: BLE001 - upstream failures must not 500
                if entry is not None:
                    age = int(now - entry[0])
                    logger.warning(
                        "Refresh of %s failed (%s); serving %ss old data", key, exc, age
                    )
                    return entry[1]
                logger.error("Fetch of %s failed with no cached fallback: %s", key, exc)
                raise

            self._store(key, now, value)
            return value
        finally:
            refresh.release()

    def age(self, key: str) -> Optional[float]:
        entry = self._get(key)
        return None if entry is None else time.time() - entry[0]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._refreshing.clear()

    def _get(self, key: str) -> Optional[Tuple[float, Any]]:
        with self._lock:
            return self._data.get(key)

    def _refresh_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._refreshing.get(key)
            if lock is None:
                lock = self._refreshing[key] = threading.Lock()
            return lock

    def _store(self, key: str, stamp: float, value: Any) -> None:
        with self._lock:
            self._data[key] = (stamp, value)
            if self._max_entries is None or len(self._data) <= self._max_entries:
                return
            # Oldest first. Insertion order is fetch order, and an entry is only
            # ever written once per refresh, so the first key is the coldest.
            for old in list(self._data)[: len(self._data) - self._max_entries]:
                del self._data[old]
                lock = self._refreshing.get(old)
                # A lock in use belongs to a fetch still running for that key.
                if lock is not None and not lock.locked():
                    del self._refreshing[old]


cache = TTLCache()

#: Decoded GRIB/HDF5 fields: one COSMO field is a few hundred KB to a few MB,
#: and the map card can ask for many forecast hours of several of them.
#: Capped so a run through the animation cannot walk the container into its
#: memory limit; evicted fields are re-fetched from IMGW if asked for again.
field_cache = TTLCache(max_entries=32)


def fetch_bytes(url: str, timeout: Optional[int] = None) -> bytes:
    response = get_session().get(url, timeout=timeout or settings.request_timeout)
    response.raise_for_status()
    return response.content


def fetch_json(url: str, timeout: Optional[int] = None) -> Any:
    response = get_session().get(url, timeout=timeout or settings.request_timeout)
    response.raise_for_status()
    return response.json()
