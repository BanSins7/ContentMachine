"""UA31 small real PostgreSQL17 set-based load smoke (not performance SLA).

Only when CM_TEST_PG_DSN points at a disposable PostgreSQL17 distinct from
CM_DB_DSN. 200 blocked rows validate real SQL, FK/trigger and keyset/cleanup;
10k/50k remain EXPLICIT user-run benchmark CLI, not automatic tests.
"""
from __future__ import annotations

import os
import unittest

from intelligence.unified_analytics import scale_benchmark as bench
from test_unified_analytics_transactional_persistence import (
    TransactionalPersistencePG17,
)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PG17 CM_TEST_PG_DSN required")
class BenchmarkSmokePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.f=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )

    def test_real_200_blocked_rows_and_keyset_then_full_cleanup(self):
        with self.f.connect() as db:
            before=db.execute(
                "SELECT count(*) AS n FROM sources WHERE id LIKE 'uae31-%'"
            ).fetchone()["n"]
            result=bench.run(db,rows=200,batch=40)
            self.assertEqual(result["rows"],200)
            self.assertEqual(result["scanned"],200)
            self.assertEqual(result["qualified_records"],0)
            self.assertFalse(result["qualified_e2e_throughput_measured"])
            after=db.execute(
                "SELECT count(*) AS n FROM sources WHERE id LIKE 'uae31-%'"
            ).fetchone()["n"]
            self.assertEqual(after,before)

    def test_internal_bad_size_cannot_open_any_db_transaction(self):
        with self.f.connect() as db:
            for rows,batch in ((50001,5000),(0,1),(200,5001),(200,0)):
                with self.subTest(rows=rows,batch=batch):
                    with self.assertRaises(ValueError):
                        bench.run(db,rows=rows,batch=batch)

    def test_real_small_blocked_fixture_does_not_qualify_or_publish(self):
        with self.f.connect() as db:
            result=bench.run(db,rows=25,batch=5)
            self.assertEqual(result["qualified_records"],0)
            self.assertEqual(result["fixture"],
                             "unqualified_blocked_no_payload_no_peer_approval")


if __name__=="__main__":
    unittest.main()
