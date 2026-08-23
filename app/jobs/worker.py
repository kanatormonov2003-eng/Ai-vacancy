"""Bounded durable worker for ingestion jobs."""
from __future__ import annotations
import threading
from ..config import load
from ..domain.query import SearchQuery, parse as parse_query
from ..runtime import ingest_source
from ..sources import base as source_base
from . import service


class RecoverableJobError(RuntimeError):
    pass


class PermanentJobError(RuntimeError):
    pass


class Worker:
    def __init__(self, worker_id: str = "worker-1", *, lease_seconds: int | None = None):
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds or load().job_lock_timeout_s
        self.stop_event = threading.Event()

    def handle(self, job: dict) -> dict:
        if job["type"] != "ingest_source":
            raise PermanentJobError("unsupported job type")
        source_base.load_all_providers()
        sources = source_base.build([job["payload"]["source"]])
        if not sources:
            raise PermanentJobError("source unavailable")
        payload = job["payload"]
        query = parse_query(payload.get("text", "")) if payload.get("text") else SearchQuery()
        result = ingest_source(sources[0], query, job["org_id"], limit=payload.get("limit", 50),
                               max_workers=payload.get("max_workers", 1))
        if result.fatal_error:
            raise RecoverableJobError(result.fatal_error)
        return result.as_dict()

    def run_once(self) -> dict | None:
        job = service.claim(self.worker_id, lease_seconds=self.lease_seconds)
        if not job:
            return None
        try:
            result = self.handle(job)
            service.complete(job["id"], self.worker_id, result)
        except PermanentJobError as exc:
            service.fail(job, self.worker_id, exc)
        except RecoverableJobError as exc:
            service.retry_or_fail(job, self.worker_id, exc)
        except Exception as exc:
            service.retry_or_fail(job, self.worker_id, exc)
        return service.get_job(job["org_id"], job["id"]) if job["org_id"] else None

    def run_until_empty(self, max_jobs: int | None = None) -> int:
        done = 0
        while not self.stop_event.is_set() and (max_jobs is None or done < max_jobs):
            if self.run_once() is None:
                break
            done += 1
        return done

    def stop(self):
        self.stop_event.set()
