import unittest
from tests.base import AppTestCase


class IngestionServiceTest(AppTestCase):
    with_fixture_server = True

    def setUp(self):
        import os
        from app import config
        os.environ["DEMO_WEBSITE_BASE"] = self.web.base
        os.environ["SOURCE_RATE_PER_MIN"] = "100000"
        config.reset_cache(); config.load(force=True)

    def raw(self, **kw):
        from app.sources.base import RawRecord
        data = dict(source="ingestion-test", external_id="r1", company_name="Кафе Альфа",
                    city="Бишкек", phone="0555 11 22 33", website=self.web.base + "/good/",
                    description="Кафе, открыли новый филиал")
        data.update(kw)
        return RawRecord(**data).sanitized()

    def test_source_to_lead_and_duplicate_provenance(self):
        from app import runtime
        from app.db import repo
        u = self.make_user("ingestion@example.kg")
        first = runtime.process_record(self.raw(), u["org_id"])
        second = runtime.process_record(self.raw(external_id="r2", description=""), u["org_id"])
        self.assertEqual(first.status, "created")
        self.assertEqual(second.status, "merged")
        rows, total = repo.search_leads(u["org_id"], q="Кафе Альфа")
        self.assertEqual((total, len(rows)), (1, 1))
        self.assertEqual(len(repo.sources_for(u["org_id"], [first.lead_id])[first.lead_id]), 2)
        stored = repo.get_lead(u["org_id"], first.lead_id)
        self.assertEqual(stored["description"], "Кафе, открыли новый филиал")

    def test_invalid_record_is_skipped_without_database_row(self):
        from app import runtime
        from app.db import repo
        u = self.make_user("invalid@example.kg")
        result = runtime.process_record(self.raw(company_name="", phone=None, website=None), u["org_id"])
        self.assertEqual(result.status, "skipped")
        self.assertEqual(repo.search_leads(u["org_id"], limit=10)[1], 0)

    def test_bad_website_is_recoverable_and_record_is_persisted(self):
        from app import runtime
        from app.db import repo
        u = self.make_user("bad-site@example.kg")
        result = runtime.process_record(self.raw(website=self.web.base + "/error500"), u["org_id"])
        self.assertEqual(result.status, "created")
        self.assertEqual(repo.get_lead(u["org_id"], result.lead_id)["website_status"], "unreachable")
        self.assertIsNotNone(repo.latest_website_analysis(u["org_id"], result.lead_id))


if __name__ == "__main__":
    unittest.main()
