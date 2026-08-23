"""System-level audit tests for the interfaces that currently exist.

The archive has no ingestion/job/API runner. These tests compose the real modules
at their boundaries and keep that missing runtime explicit instead of simulating it.
"""
from __future__ import annotations
import sqlite3
import threading
import unittest
from tests.base import AppTestCase


class E2EPipelineAuditTest(AppTestCase):
    with_fixture_server = True

    def setUp(self):
        from app.analysis import circuit, http_client
        from app.db import sqlite as db
        from app import config
        import os
        os.environ["DEMO_WEBSITE_BASE"] = self.web.base
        config.reset_cache(); config.load(force=True)
        circuit.reset(); http_client.BUDGET.reset(); http_client.clear_robots_cache()
        db.execute("DELETE FROM http_cache")

    def raw(self, **overrides):
        from app.sources.base import RawRecord
        values = dict(source="e2e_fixture", external_id="e2e-1",
                      company_name="ОсОО Кафе Альфа", category="ресторан", city="bishkek",
                      phone="0555 11 22 33", website=self.web.base + "/good/",
                      instagram="cafe.alfa.kg",
                      description="Кафе и доставка еды. Открыли новый филиал, идет набор персонала.")
        values.update(overrides)
        return RawRecord(**values).sanitized()

    def materialize(self, raw, org_id):
        """Compose existing modules only; this is not an alternate pipeline."""
        from app.analysis import website
        from app.db import repo
        from app.domain import pipeline, scoring, signals
        lead = pipeline.normalize_record(raw)
        self.assertTrue(pipeline._valid(lead))
        lead_id = repo.insert_lead(org_id, pipeline._payload(lead))
        repo.add_source_ref(org_id, lead_id, raw.source, raw.source_url, raw.external_id,
                            raw.__dict__, raw.is_demo)
        analysis = None
        if lead.get("website"):
            analysis = website.analyze(lead["website"], cache_ttl_s=0).as_dict()
            repo.save_website_analysis(org_id, lead_id, analysis)
            for fact in analysis["facts"]:
                repo.upsert_fact(org_id, lead_id, fact["fact"], fact["value"], fact["source"],
                                 fact.get("source_url"), fact.get("confidence", .5))
        found = (signals.from_profile(lead)
                 + signals.from_text(lead.get("description"), raw.source, raw.source_url)
                 + signals.from_website(analysis))
        for item in found:
            repo.upsert_signal(org_id, lead_id, item["signal"], item["source"],
                               polarity=item["polarity"], source_url=item.get("source_url"),
                               evidence=item.get("evidence"), confidence=item.get("confidence", .5))
        score = scoring.score_lead(lead, found, analysis)
        quality = scoring.data_quality(lead, analysis)
        repo.save_score(org_id, lead_id, score["score"], score["reasons"], score["confidence"],
                        score["weights_version"], score["ai_adjustment"])
        repo.update_lead(org_id, lead_id, {
            "lead_score": score["score"], "score_confidence": score["confidence"],
            "data_quality_score": quality["data_quality_score"],
            "contact_confidence": quality["contact_confidence"],
            "website_confidence": quality["website_confidence"],
            "website_status": "ok" if analysis and analysis["reachable"] else
                              "unknown" if analysis is None else "unreachable",
            "website_score": analysis["total_score"] if analysis else None,
            "website_response_ms": analysis["response_ms"] if analysis else None,
        })
        return lead_id, lead, analysis, found, score, quality

    def test_01_source_to_final_lead_quality(self):
        from app.db import repo
        u = self.make_user("quality@example.kg")
        lid, lead, analysis, found, score, quality = self.materialize(self.raw(), u["org_id"])
        stored = repo.get_lead(u["org_id"], lid)
        self.assertEqual(stored["company_name"], "ОсОО Кафе Альфа")
        self.assertEqual(stored["phone_normalized"], "+996555112233")
        self.assertEqual(stored["website"], self.web.base + "/good/")
        self.assertEqual(stored["website_domain"], "127.0.0.1")  # host field is not an identity
        self.assertTrue(lead["dedupe_key"] and analysis["reachable"] and found)
        self.assertEqual(stored["lead_score"], score["score"])
        self.assertEqual(stored["score_confidence"], score["confidence"])
        self.assertEqual(stored["data_quality_score"], quality["data_quality_score"])
        self.assertIsNotNone(repo.latest_score(u["org_id"], lid))
        self.assertIsNotNone(repo.latest_website_analysis(u["org_id"], lid))

    def test_02_bad_website_signals_and_final_group_cap(self):
        from app.db import repo
        u = self.make_user("bad@example.kg")
        lid, _, analysis, found, score, _ = self.materialize(self.raw(
            external_id="bad-1", company_name="СТО Турбо", category="сто", city="Ош",
            phone="0312 900 900", website=self.web.base + "/bad/", instagram=None,
            description="Ремонт авто. Заявки принимаем только по телефону."), u["org_id"])
        names = {x["signal"] for x in found}
        self.assertTrue(analysis["reachable"])
        self.assertTrue({"poor_mobile_experience", "outdated_website"} <= names)
        website_codes = {"website_unreachable", "poor_mobile_experience", "outdated_website",
                         "no_online_catalog", "no_online_ordering", "no_contact_form", "no_https",
                         "slow_website", "no_website"}
        self.assertLessEqual(sum(x["points"] for x in score["reasons"] if x["code"] in website_codes), 34)
        self.assertEqual(repo.get_lead(u["org_id"], lid)["lead_score"], score["score"])
        self.assertTrue(any("website_weakness" in x["label"] for x in score["reasons"]))

    def test_03_modern_fixture_detection_and_missing_tls_fixture_are_explicit(self):
        u = self.make_user("modern@example.kg")
        _, _, analysis, found, score, _ = self.materialize(self.raw(external_id="modern-1"), u["org_id"])
        names = {x["signal"] for x in found}
        self.assertGreaterEqual(analysis["total_score"], 55)
        self.assertTrue(analysis["detected"]["catalog"] and analysis["detected"]["online_order"])
        self.assertTrue(analysis["detected"]["contact_form"])
        self.assertIn("modern_website", names)
        self.assertNotIn("poor_mobile_experience", names)
        self.assertNotIn("no_online_catalog", names)
        self.assertNotIn("no_online_ordering", names)
        self.assertNotIn("no_contact_form", names)
        self.assertIn("no_https", names)  # supplied local fixture is HTTP-only
        self.assertGreater(score["score"], 0)

    def test_04_social_only_presence_and_identity(self):
        from app.db import repo
        u = self.make_user("social@example.kg")
        lid, lead, analysis, found, score, _ = self.materialize(self.raw(
            external_id="social-1", company_name="Магазин Дайыр", category="магазин",
            website=None, instagram="daiyr.shop", description="Продаем только через Instagram."), u["org_id"])
        names = {x["signal"] for x in found}
        self.assertIsNone(analysis)
        self.assertIn("social_only_presence", names)
        self.assertIn("active_social", {x["code"] for x in score["reasons"]})
        self.assertTrue(lead["dedupe_key"].startswith("p:"))
        self.assertNotEqual(lead["dedupe_key"], "i:daiyr.shop")
        self.assertEqual(repo.get_lead(u["org_id"], lid)["instagram"], "daiyr.shop")
        self.assertGreaterEqual(score["confidence"], .4)

    def test_05_growth_signals_traceable_after_cap(self):
        from app.db import repo
        u = self.make_user("growth@example.kg")
        lid, _, _, found, score, _ = self.materialize(self.raw(
            external_id="growth-1", branches_estimate=5, employees_estimate=80,
            description="Открыли новый филиал, расширяемся, идет набор персонала, активная реклама."), u["org_id"])
        expected = {"multiple_branches", "new_branch", "expansion", "hiring", "advertising"}
        persisted = {x["signal"] for x in repo.signals_for(u["org_id"], [lid])[lid]}
        self.assertTrue(expected <= persisted)
        growth = {"new_branch", "expansion", "hiring", "advertising", "new_product", "rebranding", "export_activity"}
        self.assertLessEqual(sum(x["points"] for x in score["reasons"] if x["code"] in growth), 22)

    def test_06_insufficient_data_is_stable_and_queryable(self):
        from app.db import repo
        u = self.make_user("thin@example.kg")
        lid, lead, analysis, found, score, quality = self.materialize(self.raw(
            external_id="thin-1", company_name="Минимальная Компания", category=None, city=None,
            phone=None, website=None, instagram=None, description=None), u["org_id"])
        self.assertIsNone(analysis)
        self.assertTrue(lead["normalized_name"])
        self.assertIn("insufficient_data", {x["code"] for x in score["reasons"]})
        self.assertIsInstance(score["score"], int)
        self.assertLessEqual(score["confidence"], .99)
        self.assertLess(quality["data_quality_score"], .6)
        rows, total = repo.search_leads(u["org_id"], q="Минимальная", limit=10)
        self.assertEqual((total, rows[0]["id"]), (1, lid))

    def test_07_duplicate_sources_merge_without_erasing_good_fields(self):
        from app.db import repo
        from app.domain import dedupe, pipeline
        u = self.make_user("dedupe@example.kg")
        first = self.raw(external_id="dup-1", description="Первичное описание", email="one@example.kg")
        second = self.raw(external_id="dup-2", company_name="Кафе Альфа", description="", email=None, category=None, city=None)
        lid, first_lead, *_ = self.materialize(first, u["org_id"])
        incoming = pipeline.normalize_record(second)
        match = dedupe.best_match(incoming, repo.find_candidates(u["org_id"], incoming))
        self.assertTrue(match.should_merge)
        patch, _ = dedupe.merge_fields(repo.get_lead(u["org_id"], lid), incoming)
        if patch: repo.update_lead(u["org_id"], lid, patch)
        repo.add_source_ref(u["org_id"], lid, second.source, second.source_url, second.external_id, second.__dict__, second.is_demo)
        rows, total = repo.search_leads(u["org_id"], q="Кафе Альфа", limit=10)
        stored = repo.get_lead(u["org_id"], lid)
        self.assertEqual((total, len(rows)), (1, 1))
        self.assertEqual(stored["description"], first_lead["description"])
        self.assertEqual(stored["email"], "one@example.kg")
        self.assertEqual(len(repo.sources_for(u["org_id"], [lid])[lid]), 2)

    def test_08_concurrent_unique_identity_exposes_missing_recovery_layer(self):
        from app.db import repo, sqlite as db
        from app.domain import pipeline
        u = self.make_user("concurrency@example.kg")
        lead = pipeline.normalize_record(self.raw(external_id="concurrent"))
        barrier, results = threading.Barrier(2), []
        def worker():
            try:
                barrier.wait()
                results.append(("ok", repo.insert_lead(u["org_id"], pipeline._payload(lead))))
            except sqlite3.IntegrityError as exc:
                results.append(("integrity_error", str(exc)))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sorted(x[0] for x in results), ["integrity_error", "ok"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE org_id=? AND dedupe_key=?", (u["org_id"], lead["dedupe_key"])), 1)
        # Unique storage works; no existing process function catches the error.

    def test_09_tenant_scoped_query_and_mutation_boundaries(self):
        from app.db import repo
        from app.errors import NotFoundError
        a, b = self.make_user("tenant-a@example.kg"), self.make_user("tenant-b@example.kg")
        aid, *_ = self.materialize(self.raw(external_id="tenant-a"), a["org_id"])
        bid, *_ = self.materialize(self.raw(external_id="tenant-b"), b["org_id"])
        ar, _ = repo.search_leads(a["org_id"], q="Кафе Альфа", limit=10)
        br, _ = repo.search_leads(b["org_id"], q="Кафе Альфа", limit=10)
        self.assertEqual({x["id"] for x in ar}, {aid}); self.assertEqual({x["id"] for x in br}, {bid})
        with self.assertRaises(NotFoundError): repo.get_lead(a["org_id"], bid)
        self.assertEqual(repo.update_lead(a["org_id"], bid, {"description": "cross tenant"}), 0)

    def test_10_demo_source_to_database_is_deterministic(self):
        from app.db import repo
        from app.domain import pipeline
        from app.domain.query import SearchQuery
        from app.sources.demo_kg import DemoProvider
        u = self.make_user("demo@example.kg")
        records = list(DemoProvider().fetch(SearchQuery.from_dict({"cities": ["Бишкек"]}), limit=2))
        self.assertEqual(len(records), 2)
        for r in records:
            lead = pipeline.normalize_record(r); self.assertTrue(pipeline._valid(lead))
            candidates = repo.find_candidates(u["org_id"], lead)
            if candidates:
                match = __import__("app.domain.dedupe", fromlist=["best_match"]).best_match(lead, candidates)
                self.assertTrue(match.should_merge, f"{r.external_id}: {match.confidence} {match.reasons}")
                repo.add_source_ref(u["org_id"], match.lead_id, r.source, r.source_url, r.external_id, r.__dict__, r.is_demo)
            else:
                lid = repo.insert_lead(u["org_id"], pipeline._payload(lead))
                repo.add_source_ref(u["org_id"], lid, r.source, r.source_url, r.external_id, r.__dict__, r.is_demo)
        rows, total = repo.search_leads(u["org_id"], cities=["Бишкек"], limit=10)
        self.assertEqual((total, len(rows)), (1, 1)); self.assertTrue(rows[0]["is_demo"])

    def test_11_http_source_fixture_skips_invalid_rows_and_merges_duplicates(self):
        import os
        from app import config
        from app.db import repo
        from app.domain import dedupe, pipeline
        from app.domain.query import SearchQuery
        from app.sources.http_directory import HttpDirectoryProvider
        os.environ["DIRECTORY_BASE_URL"] = self.web.base + "/directory"
        os.environ["SOURCE_RATE_PER_MIN"] = "100000"
        config.reset_cache(); config.load(force=True)
        u = self.make_user("http-source@example.kg")
        records = list(HttpDirectoryProvider().fetch(SearchQuery(), limit=20))
        self.assertEqual(len(records), 5)  # empty-name row is rejected deterministically
        seen = []
        for r in records:
            lead = pipeline.normalize_record(r)
            candidates = repo.find_candidates(u["org_id"], lead)
            match = dedupe.best_match(lead, candidates) if candidates else None
            if match and match.should_merge:
                repo.add_source_ref(u["org_id"], match.lead_id, r.source, r.source_url, r.external_id, r.__dict__, r.is_demo)
                seen.append(match.lead_id)
            else:
                lid = repo.insert_lead(u["org_id"], pipeline._payload(lead))
                repo.add_source_ref(u["org_id"], lid, r.source, r.source_url, r.external_id, r.__dict__, r.is_demo)
                seen.append(lid)
        rows, total = repo.search_leads(u["org_id"], limit=20)
        self.assertEqual((total, len(rows)), (4, 4))
        self.assertEqual(len(repo.sources_for(u["org_id"], [seen[0]])[seen[0]]), 2)

    def test_12_failure_is_structured_and_does_not_break_following_record(self):
        from app.domain import signals
        u = self.make_user("failure@example.kg")
        bad_id, _, bad_analysis, bad_found, _, _ = self.materialize(self.raw(
            external_id="error-500", website=self.web.base + "/error500", description="bad site"), u["org_id"])
        good_id, _, good_analysis, _, _, _ = self.materialize(self.raw(
            external_id="after-error", company_name="Вторая Компания", phone="0555 22 33 44"), u["org_id"])
        self.assertFalse(bad_analysis["reachable"])
        self.assertIn("website_unreachable", {x["signal"] for x in bad_found})
        self.assertIsNotNone(good_analysis)
        self.assertNotEqual(bad_id, good_id)
        self.assertIn("website_unreachable", {x["signal"] for x in signals.from_website(bad_analysis)})

    def test_13_query_filters_pagination_sort_and_injection_resistance(self):
        from app.db import repo
        from app.domain import pipeline
        u = self.make_user("query@example.kg")
        for i, city in enumerate(("Бишкек", "Ош", "Каракол")):
            lead = pipeline.normalize_record(self.raw(external_id=f"query-{i}", company_name=f"Компания {i}", city=city, phone=f"0555 11 2{i} 33", website=None, instagram=None))
            repo.insert_lead(u["org_id"], pipeline._payload(lead))
        rows, total = repo.search_leads(u["org_id"], cities=["Ош"], limit=10)
        self.assertEqual((total, rows[0]["city"]), (1, "Ош"))
        rows, total = repo.search_leads(u["org_id"], q="' OR 1=1 --", sort="drop table leads", limit=1, offset=1)
        self.assertEqual((total, rows), (0, []))
        rows, total = repo.search_leads(u["org_id"], sort="company_name", direction="asc", limit=2)
        self.assertEqual((total, len(rows)), (3, 2)); self.assertLessEqual(rows[0]["company_name"], rows[1]["company_name"])

    def test_14_auth_validation_session_and_rate_limit_interfaces(self):
        from app.api import auth, ratelimit, validation
        from app.errors import AuthError, ValidationError
        uid, oid = auth.register("auth@example.kg", "Str0ngPass!x")
        token, user = auth.login("auth@example.kg", "Str0ngPass!x", "e2e-test")
        session = auth.resolve_session(token)
        self.assertEqual((session["id"], session["org_id"], user["id"]), (uid, oid, uid))
        with self.assertRaises(AuthError): auth.resolve_session("not-a-session")
        with self.assertRaises(AuthError): auth.login("auth@example.kg", "wrong-password")
        with self.assertRaises(ValidationError): validation.validate({"extra": 1}, {})
        ratelimit.reset(); self.assertTrue(ratelimit.check("e2e-auth", 2)[0]); self.assertTrue(ratelimit.check("e2e-auth", 2)[0]); self.assertFalse(ratelimit.check("e2e-auth", 2)[0])

    def test_15_runtime_gap_is_explicit_not_simulated(self):
        from app.domain import pipeline
        from app.sources import base
        self.assertTrue(hasattr(pipeline, "normalize_record"))
        self.assertFalse(hasattr(pipeline, "run")); self.assertFalse(hasattr(pipeline, "process")); self.assertFalse(hasattr(pipeline, "ingest"))
        self.assertTrue(hasattr(base.LeadSource, "fetch"))


if __name__ == "__main__":
    unittest.main()
