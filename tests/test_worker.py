import unittest
from tests.base import AppTestCase


class WorkerTest(AppTestCase):
    with_fixture_server = True

    def setUp(self):
        import os
        from app import config
        os.environ["DEMO_WEBSITE_BASE"] = self.web.base
        os.environ["SOURCE_RATE_PER_MIN"] = "100000"
        config.reset_cache(); config.load(force=True)

    def test_worker_claims_processes_and_completes_ingestion_job(self):
        from app.db import repo
        from app.jobs import service
        from app.jobs.worker import Worker
        from app.sources.demo_kg import DemoProvider
        u = self.make_user("worker@example.kg")
        job = service.enqueue_ingestion(u["org_id"], "demo_kg", {"text": "", "limit": 1}, idempotency_key="worker-job")
        worker = Worker("worker-a")
        done = worker.run_once()
        self.assertEqual(done["status"], "done")
        self.assertEqual(repo.search_leads(u["org_id"], limit=10)[1], 1)

    def test_unsupported_job_reaches_bounded_failure(self):
        from app.jobs import service
        from app.jobs.worker import Worker
        u = self.make_user("worker-fail@example.kg")
        job = service.enqueue(u["org_id"], "unknown", {}, idempotency_key="worker-fail", max_attempts=1)
        result = Worker("worker-b").run_once()
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__": unittest.main()
