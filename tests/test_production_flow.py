import unittest
from tests.base import AppTestCase
from tests.test_http_api import call


class ProductionFlowTest(AppTestCase):
    with_fixture_server = True

    def setUp(self):
        import os
        from app import config
        os.environ["DEMO_WEBSITE_BASE"] = self.web.base
        os.environ["SOURCE_RATE_PER_MIN"] = "100000"
        config.reset_cache(); config.load(force=True)

    def test_http_job_worker_database_query_canonical_result(self):
        from app.jobs.worker import Worker
        from app.web.server import Application
        from app.jobs import service
        u = self.make_user("prod-flow@example.kg")
        app = Application()
        status, created = call(app, "POST", "/ingest/demo_kg", u["token"], {"limit": 1})
        self.assertEqual(status, 202)
        self.assertEqual(Worker("prod-flow-worker").run_once()["status"], "done")
        status, response = call(app, "GET", "/leads?limit=10", u["token"])
        self.assertEqual(status, 200)
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["items"][0]["id"], response["items"][0]["id"])
        self.assertEqual(service.get_job(u["org_id"], created["id"])["status"], "done")


if __name__ == "__main__": unittest.main()
