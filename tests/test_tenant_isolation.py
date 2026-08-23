"""Regression tests for P0-3: tenant isolation in the repository layer.

Every test here failed (or could not even be expressed) before the fix. The
core case is the audited one: repo.update_search(search_id, patch) rewrote a
foreign tenant's search row because the WHERE clause had no org_id.
"""
from __future__ import annotations
from tests.base import AppTestCase
from app.db import repo, sqlite as db
from app.errors import NotFoundError, ValidationError


class TenantIsolationTest(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = cls().make_user("a@tenant-a.kg")
        cls.b = cls().make_user("b@tenant-b.kg")

    def setUp(self):
        self.org_a, self.org_b = self.a["org_id"], self.b["org_id"]
        self.assertNotEqual(self.org_a, self.org_b)

    # ------------------------------------------------------------- helpers

    def _search(self, user, text="stroy bishkek"):
        return repo.create_search(user["org_id"], user["user_id"], text, {"city": "Bishkek"})

    def _lead(self, org_id, name="Alfa", key=None):
        return repo.insert_lead(org_id, {
            "dedupe_key": key or f"n:{name.lower()}|bishkek",
            "company_name": name, "normalized_name": name.lower(), "city": "Bishkek",
        })

    # ------------------------------------------------------------- searches

    def test_update_search_rejects_foreign_org(self):
        """P0-3, the exact audited reproduction: org B must not rewrite org A's search."""
        sid = self._search(self.a, "original text")
        with self.assertRaises(NotFoundError):
            repo.update_search(self.org_b, sid, {"query_text": "hijacked"})
        self.assertEqual(repo.get_search(self.org_a, sid)["query_text"], "original text")

    def test_update_search_works_for_the_owner(self):
        sid = self._search(self.a)
        self.assertEqual(repo.update_search(self.org_a, sid, {"status": "running"}), 1)
        self.assertEqual(repo.get_search(self.org_a, sid)["status"], "running")

    def test_update_search_serialises_json_columns(self):
        sid = self._search(self.a)
        repo.update_search(self.org_a, sid, {"stats": {"found": 3}, "sources": ["demo_kg"]})
        row = repo.get_search(self.org_a, sid)
        self.assertEqual(row["stats"], {"found": 3})
        self.assertEqual(row["sources"], ["demo_kg"])

    def test_update_search_cannot_move_row_to_another_org(self):
        sid = self._search(self.a)
        with self.assertRaises(ValidationError):
            repo.update_search(self.org_a, sid, {"org_id": self.org_b})
        self.assertEqual(repo.get_search(self.org_a, sid)["org_id"], self.org_a)

    def test_update_search_requires_org(self):
        sid = self._search(self.a)
        with self.assertRaises(ValidationError):
            repo.update_search("", sid, {"status": "running"})

    def test_update_search_unknown_id_raises(self):
        with self.assertRaises(NotFoundError):
            repo.update_search(self.org_a, "srch_missing", {"status": "running"})

    def test_create_search_rejects_foreign_user(self):
        with self.assertRaises(NotFoundError):
            repo.create_search(self.org_a, self.b["user_id"], "x", {})

    def test_create_search_rejects_foreign_parent(self):
        parent = self._search(self.b)
        with self.assertRaises(NotFoundError):
            repo.create_search(self.org_a, self.a["user_id"], "x", {}, parent_search_id=parent)

    def test_soft_delete_search_is_scoped(self):
        sid = self._search(self.a)
        with self.assertRaises(NotFoundError):
            repo.soft_delete_search(self.org_b, sid)
        self.assertEqual(repo.get_search(self.org_a, sid)["id"], sid)

    # ------------------------------------------------------------- leads

    def test_insert_lead_forces_caller_org(self):
        lead_id = repo.insert_lead(self.org_a, {
            "dedupe_key": "n:forced|bishkek", "company_name": "Forced",
            "normalized_name": "forced", "org_id": None,
        })
        self.assertEqual(repo.get_lead(self.org_a, lead_id)["org_id"], self.org_a)

    def test_insert_lead_rejects_smuggled_org(self):
        with self.assertRaises(ValidationError):
            repo.insert_lead(self.org_a, {
                "dedupe_key": "n:smuggled|bishkek", "company_name": "S",
                "normalized_name": "s", "org_id": self.org_b,
            })

    def test_insert_lead_rejects_foreign_first_search(self):
        foreign = self._search(self.b)
        with self.assertRaises(NotFoundError):
            repo.insert_lead(self.org_a, {
                "dedupe_key": "n:fs|bishkek", "company_name": "FS",
                "normalized_name": "fs", "first_search_id": foreign,
            })

    def test_get_and_update_lead_are_scoped(self):
        lead_id = self._lead(self.org_a, "Scoped", "n:scoped|bishkek")
        with self.assertRaises(NotFoundError):
            repo.get_lead(self.org_b, lead_id)
        self.assertEqual(repo.update_lead(self.org_b, lead_id, {"city": "Osh"}), 0)
        self.assertEqual(repo.get_lead(self.org_a, lead_id)["city"], "Bishkek")

    def test_update_lead_cannot_change_identity_columns(self):
        lead_id = self._lead(self.org_a, "Ident", "n:ident|bishkek")
        for bad in ({"org_id": self.org_b}, {"id": "lead_other"}, {"dedupe_key": "d:evil.kg"}):
            with self.assertRaises(ValidationError):
                repo.update_lead(self.org_a, lead_id, bad)

    # -------------------------------------------------- child tables (no org_id column)

    def test_child_writes_reject_foreign_lead(self):
        lead_id = self._lead(self.org_a, "Child", "n:child|bishkek")
        with self.assertRaises(NotFoundError):
            repo.add_source_ref(self.org_b, lead_id, "demo_kg", None, "x1", {}, False)
        with self.assertRaises(NotFoundError):
            repo.upsert_fact(self.org_b, lead_id, "phone", "+996555112233", "demo_kg", None, 0.9)
        with self.assertRaises(NotFoundError):
            repo.upsert_signal(self.org_b, lead_id, "no_website", "demo_kg")
        with self.assertRaises(NotFoundError):
            repo.lead_event(self.org_b, lead_id, "viewed")
        with self.assertRaises(NotFoundError):
            repo.save_score(self.org_b, lead_id, 70, ["r"], 0.8, "v1")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM lead_facts WHERE lead_id = ?", (lead_id,), 0), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM lead_signals WHERE lead_id = ?", (lead_id,), 0), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM lead_source_refs WHERE lead_id = ?", (lead_id,), 0), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM lead_scores WHERE lead_id = ?", (lead_id,), 0), 0)

    def test_bulk_child_reads_drop_foreign_ids(self):
        a_lead = self._lead(self.org_a, "Bulk A", "n:bulk a|bishkek")
        b_lead = self._lead(self.org_b, "Bulk B", "n:bulk b|bishkek")
        repo.upsert_fact(self.org_a, a_lead, "email", "a@a.kg", "demo_kg", None, 0.9)
        repo.upsert_fact(self.org_b, b_lead, "email", "b@b.kg", "demo_kg", None, 0.9)
        repo.upsert_signal(self.org_b, b_lead, "no_website", "demo_kg")
        repo.add_source_ref(self.org_b, b_lead, "demo_kg", None, "b1", {"x": 1}, False)
        for fn in (repo.facts_for, repo.signals_for, repo.sources_for):
            got = fn(self.org_a, [a_lead, b_lead])
            self.assertNotIn(b_lead, got, f"{fn.__name__} leaked a foreign lead")
        self.assertEqual(len(repo.facts_for(self.org_a, [a_lead, b_lead])[a_lead]), 1)

    def test_latest_child_reads_are_scoped(self):
        b_lead = self._lead(self.org_b, "Latest", "n:latest|bishkek")
        repo.save_score(self.org_b, b_lead, 81, ["good"], 0.9, "v1")
        self.assertEqual(repo.latest_score(self.org_b, b_lead)["score"], 81)
        for fn in (repo.latest_score, repo.latest_website_analysis, repo.latest_ai_analysis):
            with self.assertRaises(NotFoundError):
                fn(self.org_a, b_lead)

    def test_search_results_link_cannot_cross_tenants(self):
        a_search, b_search = self._search(self.a), self._search(self.b)
        a_lead = self._lead(self.org_a, "Link A", "n:link a|bishkek")
        b_lead = self._lead(self.org_b, "Link B", "n:link b|bishkek")
        with self.assertRaises(NotFoundError):
            repo.add_search_result(self.org_a, a_search, b_lead)
        with self.assertRaises(NotFoundError):
            repo.add_search_result(self.org_a, b_search, a_lead)
        repo.add_search_result(self.org_a, a_search, a_lead)
        self.assertEqual(len(repo.search_result_leads(self.org_a, a_search)), 1)
        with self.assertRaises(NotFoundError):
            repo.search_result_leads(self.org_b, a_search)

    def test_search_leads_ignores_foreign_search_id(self):
        b_search = self._search(self.b)
        b_lead = self._lead(self.org_b, "Only B", "n:only b|bishkek")
        repo.add_search_result(self.org_b, b_search, b_lead)
        a_lead = self._lead(self.org_a, "Only A", "n:only a|bishkek")
        rows, total = repo.search_leads(self.org_a, search_id=b_search)
        self.assertEqual((rows, total), ([], 0))
        rows, total = repo.search_leads(self.org_b, search_id=b_search)
        self.assertEqual([r["id"] for r in rows], [b_lead])
        self.assertEqual(total, 1)
        self.assertNotIn(a_lead, [r["id"] for r in rows])

    def test_search_leads_never_returns_other_tenants(self):
        self._lead(self.org_b, "Hidden", "n:hidden|bishkek")
        rows, _ = repo.search_leads(self.org_a, q="Hidden")
        self.assertEqual(rows, [])

    def test_org_scoped_user_lookup(self):
        self.assertIsNone(repo.get_org_user(self.org_a, self.b["user_id"]))
        self.assertIsNotNone(repo.get_org_user(self.org_a, self.a["user_id"]))

    def test_alert_rejects_foreign_lead(self):
        b_lead = self._lead(self.org_b, "Alerted", "n:alerted|bishkek")
        with self.assertRaises(NotFoundError):
            repo.alert(self.org_a, "lead.hot", "nope", lead_id=b_lead)

    def test_usage_counters_require_org(self):
        with self.assertRaises(ValidationError):
            repo.bump_usage("", "searches")
        repo.bump_usage(self.org_a, "searches", 2)
        self.assertEqual(repo.usage(self.org_a, "searches"), 2.0)
        self.assertEqual(repo.usage(self.org_b, "searches"), 0.0)

    # -------------------------------------------------- unsafe SQL primitive

    def test_insert_rejects_injected_column_name(self):
        with self.assertRaises(ValueError):
            db.insert("leads", {"id) VALUES ('x'); DROP TABLE leads; --": 1})
        with self.assertRaises(ValueError):
            db.update("leads", {"city": "Osh"}, "", ())
        self.assertIsNotNone(db.scalar("SELECT COUNT(*) FROM leads", (), None))
