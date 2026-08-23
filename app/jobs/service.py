"""Durable job repository on the configured DB backend."""
from __future__ import annotations
import datetime as dt
import socket
import time
from .. import obs
from ..db import sqlite as db
from ..errors import NotFoundError
from ..util import dumps, loads, new_id, now, now_iso, iso


def enqueue(org_id: str | None, job_type: str, payload: dict, *, idempotency_key: str | None = None,
            max_attempts: int = 3, run_at: str | None = None) -> dict:
    key = idempotency_key or new_id("idem")
    existing = db.one("SELECT * FROM jobs WHERE idempotency_key=?", (key,))
    if existing:
        return _row(existing)
    job_id = new_id("job")
    data = {"id": job_id, "org_id": org_id, "type": job_type, "payload": dumps(payload),
            "status": "queued", "attempts": 0, "max_attempts": max(1, min(int(max_attempts), 20)),
            "idempotency_key": key, "run_at": run_at or now_iso(), "locked_by": None,
            "locked_at": None, "last_error": None, "result": None, "created_at": now_iso(),
            "updated_at": now_iso()}
    try:
        db.insert("jobs", data)
    except Exception as exc:
        if not db.is_integrity_error(exc):
            raise
        existing = db.one("SELECT * FROM jobs WHERE idempotency_key=?", (key,))
        if not existing:
            raise
        return _row(existing)
    return _row(data)


def enqueue_ingestion(org_id: str, source: str, payload: dict, *, idempotency_key: str | None = None) -> dict:
    key = idempotency_key or f"ingest:{org_id}:{source}:{dumps(payload)}"
    return enqueue(org_id, "ingest_source", {"source": source, **payload}, idempotency_key=key)


def _row(row) -> dict:
    d = dict(row)
    d["payload"] = loads(d.get("payload"), {})
    d["result"] = loads(d.get("result"), None)
    return d


def get_job(org_id: str, job_id: str) -> dict:
    row = db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (job_id, org_id))
    if not row:
        raise NotFoundError("Job not found")
    return _row(row)


def _recover_abandoned(lease_seconds: int):
    cutoff = iso(now() - dt.timedelta(seconds=lease_seconds))
    db.update("jobs", {"status": "queued", "locked_by": None, "locked_at": None,
                        "updated_at": now_iso()}, "status='running' AND locked_at < ?", (cutoff,))


def claim(worker_id: str | None = None, *, lease_seconds: int = 120) -> dict | None:
    worker_id = worker_id or f"{socket.gethostname()}:{new_id('worker')}"
    _recover_abandoned(lease_seconds)
    with db.tx():
        if db.backend_name() == "postgresql":
            row = db.one("SELECT * FROM jobs WHERE status='queued' AND run_at <= ? "
                         "ORDER BY run_at, id LIMIT 1 FOR UPDATE SKIP LOCKED", (now_iso(),))
        else:
            row = db.one("SELECT * FROM jobs WHERE status='queued' AND run_at <= ? "
                         "ORDER BY run_at, id LIMIT 1", (now_iso(),))
        if not row:
            return None
        job_id = row["id"]
        changed = db.update("jobs", {"status": "running", "attempts": row["attempts"] + 1,
                                      "locked_by": worker_id, "locked_at": now_iso(),
                                      "updated_at": now_iso()},
                            "id=? AND status='queued'", (job_id,))
        if not changed:
            return None
        return _row(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)))


def complete(job_id: str, worker_id: str, result: dict | None = None) -> bool:
    return bool(db.update("jobs", {"status": "done", "result": dumps(result or {}),
                                    "locked_by": None, "locked_at": None,
                                    "updated_at": now_iso()},
                          "id=? AND status='running' AND locked_by=?", (job_id, worker_id)))


def fail(job: dict, worker_id: str, error: Exception) -> str:
    db.update("jobs", {"status": "failed", "last_error": type(error).__name__,
                        "locked_by": None, "locked_at": None, "updated_at": now_iso()},
              "id=? AND status='running' AND locked_by=?", (job["id"], worker_id))
    return "failed"


def retry_or_fail(job: dict, worker_id: str, error: Exception, *, backoff_base: float = 0.2) -> str:
    terminal = job["attempts"] >= job["max_attempts"]
    status = "failed" if terminal else "queued"
    run_at = now_iso() if terminal else iso(now() + dt.timedelta(seconds=min(60, backoff_base * (2 ** (job["attempts"] - 1)))))
    db.update("jobs", {"status": status, "run_at": run_at, "last_error": type(error).__name__,
                        "locked_by": None, "locked_at": None, "updated_at": now_iso()},
              "id=? AND status='running' AND locked_by=?", (job["id"], worker_id))
    return status
