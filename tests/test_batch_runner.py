import unittest
from tests.base import AppTestCase


class BatchRunnerTest(AppTestCase):
    def test_ten_records_have_bounded_isolation_and_counters(self):
        from app import runtime
        from app.sources.base import RawRecord
        u = self.make_user("batch@example.kg")
        records = [RawRecord(source="batch", external_id=str(i), company_name=f"Batch {i}",
                             phone=f"0555 11 2{i} 33") for i in range(10)]
        records[4] = RawRecord(source="batch", external_id="bad", company_name="")
        def failing_analyzer(url):
            raise RuntimeError("fixture provider failure")
        result = runtime.ingest_records(records, u["org_id"], max_workers=2,
                                        analyzer=failing_analyzer)
        self.assertEqual(sum(result.counters.values()), 10)
        self.assertEqual(result.counters["skipped"], 1)
        self.assertEqual(result.counters["created"], 9)
        self.assertEqual(result.counters["failed"], 0)
        self.assertIsNone(result.fatal_error)

    def test_source_iteration_failure_is_fatal_but_completed_records_remain(self):
        from app import runtime
        from app.sources.base import RawRecord
        u = self.make_user("batch-source-failure@example.kg")
        def records():
            yield RawRecord(source="source", external_id="ok", company_name="Before Failure", phone="0555 44 55 66")
            raise RuntimeError("source disconnected")
        result = runtime.ingest_records(records(), u["org_id"])
        self.assertEqual(result.counters["created"], 1)
        self.assertEqual(result.counters["failed"], 0)
        self.assertIn("RuntimeError", result.fatal_error)

    def test_malformed_injected_provider_result_fails_one_record_only(self):
        from app import runtime
        from app.sources.base import RawRecord
        u = self.make_user("batch-failure@example.kg")
        records = [RawRecord(source="batch", external_id=str(i), company_name=f"Site {i}",
                             phone=f"0555 22 3{i} 44", website=f"https://example{i}.com") for i in range(3)]
        calls = [0]
        def bad_for_one(url):
            calls[0] += 1
            if calls[0] == 1:
                return {"invalid": True}
            return {"url": url, "reachable": False, "final_url": url, "https": False,
                    "facts": [], "detected": {}, "scores": {}, "total_score": 0,
                    "error_code": "fixture"}
        result = runtime.ingest_records(records, u["org_id"], max_workers=1, analyzer=bad_for_one)
        self.assertEqual(result.counters["failed"], 1)
        self.assertEqual(result.counters["created"], 2)


if __name__ == "__main__":
    unittest.main()
