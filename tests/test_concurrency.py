import threading
import unittest
from tests.base import AppTestCase


class ConcurrencyRuntimeTest(AppTestCase):
    with_fixture_server = True

    def test_duplicate_workers_both_return_canonical_result(self):
        from app import runtime
        from app.db import repo, sqlite as db
        from app.sources.base import RawRecord
        u = self.make_user("concurrency-runtime@example.kg")
        raw = RawRecord(source="concurrent", external_id="same", company_name="Concurrent KG",
                        phone="0555 11 22 33", description="same record").sanitized()
        barrier, results = threading.Barrier(2), []
        def worker():
            barrier.wait()
            results.append(runtime.process_record(raw, u["org_id"]))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status in ("created", "merged") for r in results), results)
        self.assertEqual(len({r.lead_id for r in results}), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM leads WHERE org_id=?", (u["org_id"],)), 1)
        self.assertEqual(len(repo.sources_for(u["org_id"], [results[0].lead_id])[results[0].lead_id]), 1)


if __name__ == "__main__":
    unittest.main()
