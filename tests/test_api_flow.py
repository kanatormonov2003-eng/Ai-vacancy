import unittest
from tests.base import AppTestCase


class ApiFlowTest(AppTestCase):
    def test_org_id_is_not_client_selectable(self):
        from app.api import runtime
        from app.errors import ValidationError
        a, b = self.make_user("api-a@example.kg"), self.make_user("api-b@example.kg")
        with self.assertRaises(ValidationError):
            runtime.query_leads(a["token"], {"org_id": b["org_id"]})

    def test_authenticated_query_is_scoped_to_session_org(self):
        from app import runtime
        from app.api import runtime as api_runtime
        from app.sources.base import RawRecord
        a, b = self.make_user("api-scope-a@example.kg"), self.make_user("api-scope-b@example.kg")
        runtime.process_record(RawRecord(source="api", external_id="b", company_name="Private B",
                                         phone="0555 66 77 88"), b["org_id"])
        rows, total = api_runtime.query_leads(a["token"], {"q": "Private", "limit": 10})
        self.assertEqual((rows, total), ([], 0))

    def test_authentication_failure_prevents_action(self):
        from app.api import runtime
        from app.db import sqlite as db
        from app.errors import AuthError
        with self.assertRaises(AuthError):
            runtime.query_leads(None, {})
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM audit_log WHERE action='query.executed'", default=0), 0)

    def test_rate_limit_is_checked_before_query_action(self):
        import os
        from app import config
        from app.api import runtime, ratelimit
        from app.errors import RateLimitError
        os.environ["API_RATE_PER_MIN"] = "1"
        config.reset_cache(); config.load(force=True)
        try:
            u = self.make_user("rate@example.kg")
            runtime.query_leads(u["token"], {})
            with self.assertRaises(RateLimitError):
                runtime.query_leads(u["token"], {})
        finally:
            os.environ["API_RATE_PER_MIN"] = "120"
            config.reset_cache(); config.load(force=True)
            ratelimit.reset()


if __name__ == "__main__":
    unittest.main()
