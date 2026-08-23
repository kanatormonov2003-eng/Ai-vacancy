"""Structured logging + in-process metrics. Request/job/error IDs everywhere."""
from __future__ import annotations
import json, os, sys, threading, time, uuid, contextlib
from collections import defaultdict

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_local = threading.local()
_lock = threading.Lock()
_counters: dict[str, float] = defaultdict(float)
_timers: dict[str, list] = defaultdict(list)

def _ctx() -> dict:
    return getattr(_local, "ctx", {})

@contextlib.contextmanager
def context(**kw):
    prev = dict(_ctx())
    merged = {**prev, **{k: v for k, v in kw.items() if v is not None}}
    _local.ctx = merged
    try:
        yield merged
    finally:
        _local.ctx = prev

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

def log(level: str, event: str, **fields) -> None:
    min_level = _LEVELS.get(os.environ.get("LOG_LEVEL", "info"), 20)
    if _LEVELS.get(level, 20) < min_level:
        return
    if os.environ.get("LOG_SILENT") == "1":
        return
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "level": level, "event": event, **_ctx(), **fields}
    stream = sys.stderr if level in ("warn", "error") else sys.stdout
    if os.environ.get("LOG_JSON", "1") not in ("0", "false"):
        stream.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    else:
        extra = " ".join(f"{k}={v}" for k, v in rec.items() if k not in ("ts", "level", "event"))
        stream.write(f"{rec['ts']} {level.upper():5} {event} {extra}\n")
    stream.flush()

debug = lambda e, **f: log("debug", e, **f)
info = lambda e, **f: log("info", e, **f)
warn = lambda e, **f: log("warn", e, **f)
error = lambda e, **f: log("error", e, **f)

def incr(metric: str, value: float = 1.0, **labels) -> None:
    key = metric + ("|" + ",".join(f"{k}={v}" for k, v in sorted(labels.items())) if labels else "")
    with _lock:
        _counters[key] += value

def observe(metric: str, ms: float) -> None:
    with _lock:
        buf = _timers[metric]
        buf.append(ms)
        if len(buf) > 2000:
            del buf[: len(buf) - 2000]

@contextlib.contextmanager
def timed(metric: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        observe(metric, (time.perf_counter() - t0) * 1000)

def snapshot() -> dict:
    with _lock:
        timers = {}
        for k, vals in _timers.items():
            if not vals:
                continue
            s = sorted(vals)
            timers[k] = {
                "count": len(s),
                "p50_ms": round(s[len(s) // 2], 2),
                "p95_ms": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
                "max_ms": round(s[-1], 2),
            }
        return {"counters": dict(_counters), "timers": timers}

def reset() -> None:
    with _lock:
        _counters.clear()
        _timers.clear()
