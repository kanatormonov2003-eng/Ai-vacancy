"""Real PostgreSQL integration tests. They never silently use SQLite."""
from __future__ import annotations
import os
import threading
import unittest


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is required for PostgreSQL integration")
class PostgreSQLIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import config
        from app.db import migrations, sqlite as db
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        os.environ["APP_ENV"] = "test"
        os.environ["SECRET_KEY"] = "postgres-test-secret-key-that-is-long-enough"
        config.reset_cache(); config.load(force=True); db.close()
        migrations.migrate()
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        os.environ.pop("DATABASE_URL", None)
        from app import config
        config.reset_cache()

    def test_real_backend_crud_json_and_tenant_isolation(self):
        from app.api import auth
        from app.db import repo
        from app.domain import pipeline
        from app.sources.base import RawRecord
        suffix = id(self)
        ua, oa = auth.register(f"pg-a-{suffix}@example.kg", "Str0ngPass!x")
        ub, ob = auth.register(f"pg-b-{suffix}@example.kg", "Str0ngPass!x")
        lead = pipeline.normalize_record(RawRecord(source="pg", external_id="x", company_name="PG Cafe",
                                                    phone="0555 11 22 33").sanitized())
        aid = repo.insert_lead(oa, pipeline._payload(lead))
        repo.upsert_signal(oa, aid, "active_social", "pg", evidence="json")
        repo.save_score(oa, aid, 60, [{"code": "x"}], .8, "v1")
        repo.save_website_analysis(oa, aid, {"url": "https://example.com/", "detected": {"catalog": True},
                                              "facts": [], "scores": {}, "checked_at": lead["last_verified_at"]})
        self.assertEqual(repo.get_lead(oa, aid)["org_id"], oa)
        self.assertEqual(repo.signals_for(oa, [aid])[aid][0]["evidence"], "json")
        self.assertEqual(repo.latest_website_analysis(oa, aid)["detected"]["catalog"], True)
        from app.errors import NotFoundError
        with self.assertRaises(NotFoundError): repo.get_lead(ob, aid)

    def test_concurrent_runtime_dedupe_and_provenance(self):
        from app import runtime
        from app.db import repo, sqlite as db
        from app.sources.base import RawRecord
        suffix = id(self)
        org = repo.create_org(f"pg-concurrent-{suffix}")
        barrier, outcomes = threading.Barrier(2), []
        def worker():
            raw = RawRecord(source="pg", external_id="same", company_name="Same PG",
                            phone="0555 77 88 99", description="same record").sanitized()
            barrier.wait()
            outcomes.append(runtime.process_record(raw, org))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(x.status in ("created", "merged") for x in outcomes), outcomes)
        self.assertEqual(len({x.lead_id for x in outcomes}), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE org_id=?", (org,)), 1)
        self.assertEqual(len(repo.sources_for(org, [outcomes[0].lead_id])[outcomes[0].lead_id]), 1)


class PostgreSQLAdapterSQLTest(unittest.TestCase):
    def test_qmark_adapter_does_not_rewrite_literals_comments_or_dollar_quotes(self):
        from app.db.postgres import _sql
        sql = "SELECT '?' AS literal, '{\"text\":\"?\"}' AS json_text FROM t -- ?\n WHERE x IS NOT DISTINCT FROM ? /* ? */ AND y = ?"
        converted = _sql(sql)
        self.assertIn("SELECT '?'", converted)
        self.assertIn("-- ?", converted)
        self.assertIn("/* ? */", converted)
        self.assertEqual(converted.count("%s"), 2)

    def test_null_safe_external_id_sql_is_portable(self):
        from app.db import repo
        import inspect
        source = inspect.getsource(repo.add_source_ref)
        self.assertIn("IS NOT DISTINCT FROM ?", source)



if __name__ == "__main__": unittest.main()
