import datetime as dt
import unittest
from tests.base import AppTestCase


class DurableJobsTest(AppTestCase):
    def test_enqueue_is_idempotent_and_claim_is_single(self):
        from app.jobs import service
        u = self.make_user("jobs@example.kg")
        a = service.enqueue(u["org_id"], "noop", {}, idempotency_key="same-job")
        b = service.enqueue(u["org_id"], "noop", {}, idempotency_key="same-job")
        self.assertEqual(a["id"], b["id"])
        claimed = service.claim("worker-a")
        self.assertEqual(claimed["id"], a["id"])
        self.assertIsNone(service.claim("worker-b"))
        self.assertTrue(service.complete(a["id"], "worker-a", {"ok": True}))
        self.assertEqual(service.get_job(u["org_id"], a["id"])["status"], "done")

    def test_retry_is_bounded(self):
        from app.jobs import service
        u = self.make_user("retry@example.kg")
        service.enqueue(u["org_id"], "noop", {}, idempotency_key="retry", max_attempts=2)
        job = service.claim("worker")
        self.assertEqual(service.retry_or_fail(job, "worker", RuntimeError("x"), backoff_base=0), "queued")
        job = service.claim("worker")
        self.assertEqual(service.retry_or_fail(job, "worker", RuntimeError("x"), backoff_base=0), "failed")

    def test_abandoned_running_job_is_requeued(self):
        from app.db import sqlite as db
        from app.jobs import service
        from app.util import iso, now, now_iso
        u = self.make_user("lease@example.kg")
        job = service.enqueue(u["org_id"], "noop", {}, idempotency_key="lease")
        claimed = service.claim("dead-worker")
        old = iso(now() - dt.timedelta(seconds=1000))
        db.update("jobs", {"locked_at": old}, "id=?", (claimed["id"],))
        again = service.claim("new-worker", lease_seconds=10)
        self.assertEqual(again["id"], job["id"])
        self.assertEqual(again["locked_by"], "new-worker")


if __name__ == "__main__": unittest.main()
