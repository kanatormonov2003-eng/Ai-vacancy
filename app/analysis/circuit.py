"""Circuit breaker persisted in the DB so workers and API share provider state."""
from __future__ import annotations
import datetime as dt
from .. import obs
from ..db import sqlite as db
from ..errors import CircuitOpenError
from ..util import iso, now, now_iso, parse_iso

FAILURE_THRESHOLD = 5
OPEN_SECONDS = 60

def _row(provider: str) -> dict:
    row = db.one("SELECT * FROM provider_health WHERE provider = ?", (provider,))
    if row:
        return dict(row)
    db.execute("INSERT INTO provider_health (provider, state, updated_at) VALUES (?,?,?) "
               "ON CONFLICT(provider) DO NOTHING",
               (provider, "closed", now_iso()))
    return dict(db.one("SELECT * FROM provider_health WHERE provider = ?", (provider,)))

def state(provider: str) -> str:
    row = _row(provider)
    if row["state"] != "open":
        return row["state"]
    opened = parse_iso(row["opened_at"])
    if opened and (now() - opened).total_seconds() >= OPEN_SECONDS:
        db.update("provider_health", {"state": "half_open", "updated_at": now_iso()}, "provider = ?", (provider,))
        return "half_open"
    return "open"

def guard(provider: str) -> None:
    if state(provider) == "open":
        obs.incr("circuit.rejected", provider=provider)
        raise CircuitOpenError(f"Provider '{provider}' is temporarily disabled after repeated failures")

def record_success(provider: str) -> None:
    _row(provider)
    db.update("provider_health", {"state": "closed", "failures": 0, "opened_at": None,
                                  "successes": (_row(provider)["successes"] or 0) + 1,
                                  "updated_at": now_iso()}, "provider = ?", (provider,))

def record_failure(provider: str, error_code: str) -> None:
    row = _row(provider)
    failures = (row["failures"] or 0) + 1
    patch = {"failures": failures, "last_error": error_code[:200], "updated_at": now_iso()}
    if failures >= FAILURE_THRESHOLD or row["state"] == "half_open":
        patch["state"] = "open"
        patch["opened_at"] = now_iso()
        obs.warn("circuit.opened", provider=provider, failures=failures, error_code=error_code)
    db.update("provider_health", patch, "provider = ?", (row["provider"],))

def reset(provider: str | None = None) -> None:
    if provider:
        db.execute("DELETE FROM provider_health WHERE provider = ?", (provider,))
    else:
        db.execute("DELETE FROM provider_health")

def all_health() -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM provider_health ORDER BY provider")]
