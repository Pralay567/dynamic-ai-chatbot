"""A small in-memory sliding-window rate limiter.

Good enough for a single-process local/demo deployment. State lives in this
process only: it resets on restart and is NOT shared between worker processes.
If you later run several workers, move this to a shared store.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10000,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, key: str) -> Tuple[bool, int]:
        """Record a hit for ``key``.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is 0
        when the request is allowed.
        """
        if not self.enabled:
            return True, 0
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(self.window - (now - hits[0])))
                return False, retry_after
            hits.append(now)
            if len(self._hits) > self._max_keys:
                self._prune(cutoff)
            return True, 0

    def _prune(self, cutoff: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or d[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
