import unittest
from tests.base import AppTestCase


class RuntimeEntryPointTest(AppTestCase):
    with_fixture_server = True

    def test_real_source_to_database_to_query_entrypoint(self):
        import os
        from app import config
        from app.api import runtime as api_runtime
        from app.domain.query import SearchQuery
        from app.sources.demo_kg import DemoProvider
        os.environ["DEMO_WEBSITE_BASE"] = self.web.base
        os.environ["SOURCE_RATE_PER_MIN"] = "100000"
        config.reset_cache(); config.load(force=True)
        u = self.make_user("runtime@example.kg")
        batch = api_runtime.ingest_source(u["token"], DemoProvider(), {"text": "", "limit": 1})
        self.assertEqual(batch.counters["created"], 1)
        rows, total = api_runtime.query_leads(u["token"], {"cities": ["Бишкек"], "limit": 10})
        self.assertEqual((total, len(rows)), (1, 1))
        self.assertTrue(rows[0]["is_demo"])


if __name__ == "__main__":
    unittest.main()
