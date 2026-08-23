"""In-process token bucket. Keyed by identity+route class; safe under threads."""
from __future__ import annotations
import threading, time

_lock = threading.Lock()
_buckets: dict[str, tuple[float, float]] = {}

def check(key: str, limit_per_min: int, cost: float = 1.0) -> tuple[bool, float]:
    """Returns (allowed, retry_after_seconds)."""
    if limit_per_min <= 0:
        return True, 0.0
    rate = limit_per_min / 60.0
    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, (float(limit_per_min), now))
        tokens = min(float(limit_per_min), tokens + (now - last) * rate)
        if tokens < cost:
            retry = (cost - tokens) / rate
            _buckets[key] = (tokens, now)
            return False, round(retry, 2)
        _buckets[key] = (tokens - cost, now)
        return True, 0.0

def reset() -> None:
    with _lock:
        _buckets.clear()
