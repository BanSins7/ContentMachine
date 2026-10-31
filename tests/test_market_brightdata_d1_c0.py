"""Guard the REAL D1 Bright Data import time semantics and Group C C0 contract.

All tests are offline: fake DB + fake source policy, no API / no charge.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from intelligence.market_intelligence import brightdata_jobs as jobs
from intelligence.market_intelligence.group_c_quality import validate_history

URL="https://www.tiktok.com/@luke.swan89/video/7701927285129055520"
SNAPSHOT="sd_mvwt0nki3kl9vmqz6"
SOURCE="mi_brightdata_tiktok_v1"
PROJECT="a"*32


class _Rows:
    def __init__(self,one=None):
        self.one=one
    def fetchone(self):
        return self.one


class _FakeDB:
    def __init__(self):
        self.metric_insert=None
        self.numeric_insert=None
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def transaction(self):
        return nullcontext()
    def execute(self,sql,args=()):
        if "FROM jobs WHERE id=%s FOR UPDATE" in sql:
            return _Rows(("running",1))
        if "SELECT 1 FROM project_registry" in sql:
            return _Rows((1,))
        if "SELECT v.id,v.creator_id FROM videos v" in sql:
            return _Rows((42,None))
        if "SELECT ap.profile_url FROM mi_author_publications ap" in sql:
            return _Rows(None)
        if "UPDATE videos SET published_at" in sql:
            return _Rows(None)
        if "INSERT INTO metric_snapshots(" in sql:
            self.metric_insert=(sql,args)
            return _Rows((99,))
        if "INSERT INTO mi_brightdata_numeric_fields(" in sql:
            self.numeric_insert=(sql,args)
            return _Rows(None)
        if "UPDATE jobs SET state='completed'" in sql:
            return _Rows(None)
        raise AssertionError("Unexpected SQL from Bright Data importer: "+sql[:75])


class D1IngestTimestampContracts(unittest.TestCase):
    def _run(self):
        db=_FakeDB()
        payload={"platform":"tiktok","urls":[URL],"project_id":PROJECT}
        records=[{"url":URL,"create_time":"2026-10-29T03:31:59Z",
                  "play_count":13700,"digg_count":1570,
                  "comment_count":15,"share_count":91,
                  "collect_count":137,"num_share_count":91}]
        with patch("intelligence.market_intelligence.brightdata_jobs.d1.connect",
                   return_value=db),patch(
                "intelligence.market_intelligence.brightdata_jobs._ensure_source",
                return_value=(SOURCE,1)),patch(
                "intelligence.market_intelligence.brightdata_jobs.bd.trigger",
                side_effect=AssertionError("paid trigger forbidden")) as trigger:
            imported=jobs._ingest(14,1,payload,SNAPSHOT,records)
            trigger.assert_not_called()
        self.assertEqual(imported,1)
        self.assertIsNotNone(db.metric_insert)
        self.assertIsNotNone(db.numeric_insert)
        return db

    def test_real_import_marks_local_now_as_assumed_observation(self):
        db=self._run()
        sql,args=db.metric_insert
        self.assertIn("now()",sql)
        self.assertIn(",'ok',TRUE,",sql.replace("\n","").replace(" ",""))
        # An exact public counter is not proof of the time it was measured.
        self.assertIn("observation_time_assumed",sql)
        self.assertEqual(args[3:8],(13700,1570,15,91,137))

    def test_real_import_uses_group_c_contract_metric_unit(self):
        db=self._run()
        sql,args=db.metric_insert
        self.assertIn("source_metric_definition",sql)
        self.assertEqual(args[8],"platform_post_views")
        self.assertEqual(args[9],"brightdata_on_demand_v1")
        self.assertEqual(args[0],42)
        self.assertEqual(args[1],SOURCE)
        self.assertEqual(args[2],1)

    def test_import_does_not_claim_c0_without_source_timestamp(self):
        db=self._run()
        _,args=db.metric_insert
        doc={"video_id":42,"metric_source_id":SOURCE,
             "metric_snapshot_id":99}
        observed=datetime.now(timezone.utc)
        snapshot={"id":99,"source_id":SOURCE,
                  "source_metric_definition":args[8],
                  "source_metric_version":args[9],
                  "source_policy_version_id":1,
                  "views":13700,"likes":1570,"comments":15,"shares":91,
                  "data_quality_status":"ok","precision":"exact",
                  "observation_time_assumed":True,
                  "observed_at":observed.isoformat(),
                  "collected_at":observed.isoformat(),
                  "expires_at":None}
        quality=validate_history(doc,[snapshot],strict=True)
        self.assertEqual(quality["state"],"unavailable")
        self.assertEqual(quality["reason"],"observation_time_assumed")

    def test_d1_numeric_fields_are_stored_separately(self):
        db=self._run()
        sql,args=db.numeric_insert
        self.assertEqual(args[0],99)
        self.assertEqual(args[1],PROJECT)
        self.assertEqual(args[2],42)
        self.assertIn('"play_count": 13700',args[5])
        self.assertNotIn("luke.swan89",args[5])
        self.assertNotIn("comment_text",args[5])


if __name__=="__main__":
    unittest.main()
