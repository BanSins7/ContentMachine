"""GC-MOD-05: canonical D1 and source rights negative/PG17 tests.

No test may manufacture actual provider metrics outside disposable PG17.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.d1 import (
    PostgreSQLD1FactsReader, PostgreSQLCreatorTopicRegistry,
    PostgreSQLSourcePolicyReader, _int_id, _utc_time,
)
from intelligence.group_c.ports import (
    CapabilityState, GroupCPorts, PORT_SCHEMAS,
)

_NOW = datetime.now(timezone.utc)


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows if rows is not None else ([row] if row is not None else [])
        self.description = []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _FakeDB:
    """Only cursor mechanics are fake; this does NOT claim provider readiness."""
    def __init__(self, records=None, available=True, bad_columns=None):
        self.records = records or {}
        self.available = available
        self.bad_columns = set(bad_columns or [])
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "to_regclass" in sql:
            return _Cursor({"rel": "table" if self.available else None})
        if "information_schema.columns" in sql:
            name = params[0]
            # Test probes individual canonical physical column contracts.
            from intelligence.group_c.d1 import _REQUIRED
            all_columns = set().union(*(m.get(name, set()) for m in _REQUIRED.values()))
            return _Cursor(rows=[{"column_name": col} for col in all_columns
                                 if (name, col) not in self.bad_columns])
        for key, rows in self.records.items():
            if key in sql:
                if isinstance(rows, list):
                    return _Cursor(rows=rows, row=rows[0] if rows else None)
                return _Cursor(row=rows)
        return _Cursor()


class D1ReadersPureTests(unittest.TestCase):
    def test_never_default_to_ready_if_schema_missing_or_incompatible(self):
        for port, adapter in (
            ("d1", PostgreSQLD1FactsReader()),
            ("registry", PostgreSQLCreatorTopicRegistry()),
            ("source_policy", PostgreSQLSourcePolicyReader()),
        ):
            with self.subTest(port=port):
                self.assertEqual(adapter.capability(_FakeDB(available=False)).state,
                                 CapabilityState.UNAVAILABLE)
                self.assertEqual(
                    adapter.capability(_FakeDB(bad_columns={
                        ("videos" if port!="source_policy" else "sources", "id")
                    })).state, CapabilityState.INCOMPATIBLE)
                cap=adapter.capability(_FakeDB())
                self.assertEqual(cap.state, CapabilityState.AVAILABLE)
                self.assertEqual(cap.observed_schema, PORT_SCHEMAS[port])
                self.assertIs(GroupCPorts(**{port: adapter}).require(port, _FakeDB()),adapter)

    def test_invalid_ids_and_timestamps(self):
        for bad in (0, -1, True, "12", None, 1.2):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _int_id(bad,"video_id")
        for bad in (None, "2020", datetime(2020,1,1)):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _utc_time(bad,"observed_at")

    def test_no_fallback_for_missing_video_snapshot_metadata_or_project(self):
        db=_FakeDB()
        d=PostgreSQLD1FactsReader()
        with self.assertRaises(ContractError):
            d.read_video(db, 9)
        with self.assertRaises(ContractError):
            d.read_snapshot(db, 9, 3)
        with self.assertRaises(ContractError):
            d.read_publication(db, 9, 5)
        r=PostgreSQLCreatorTopicRegistry()
        with self.assertRaises(ContractError):
            r.read_project_video(db,"a"*32,9)
        with self.assertRaises(ContractError):
            r.read_creator(db,1)
        with self.assertRaises(ContractError):
            r.read_topic(db,1)
        with self.assertRaises(ContractError):
            r.read_project_video(db,"A"*32,1)

    def test_c_content_type_never_derived_from_old_kind(self):
        row=dict(id=9,platform="youtube",platform_video_id="real-id",
                 creator_id=None,creator_platform=None,canonical_url="https://example.test",
                 content_kind="video",c_content_type=None,
                 published_at=None,duration_seconds=None,language=None)
        r=PostgreSQLD1FactsReader().read_video(_FakeDB({"FROM videos v":row}),9)
        self.assertIsNone(r["c_content_type"])
        self.assertEqual(r["content_kind"],"video")
        row["creator_id"]=8
        row["creator_platform"]="tiktok"
        with self.assertRaises(ContractError) as err:
            PostgreSQLD1FactsReader().read_video(_FakeDB({"FROM videos v":row}),9)
        self.assertEqual(err.exception.code,"creator_platform_mismatch")

    def test_raw_snapshot_does_not_coerce_unknown_to_zero(self):
        original=dict(
            id=9,video_id=3,source_id="d1-provider",source_policy_version_id=11,
            observed_at=_NOW,collected_at=_NOW,views=None,likes=0,comments=None,
            shares=2,saves=None,followers=None,precision="unknown",expires_at=None,
            source_metric_definition="reach",source_metric_version="r1",
            observation_time_assumed=False,data_quality_status="partial",
            snapshot_key=None,
        )
        out=PostgreSQLD1FactsReader().read_snapshot(
            _FakeDB({"FROM metric_snapshots WHERE id":original}),3,9)
        self.assertIsNone(out["views"])
        self.assertIsNone(out["comments"])
        self.assertEqual(out["likes"],0)
        self.assertEqual(out["source_metric_definition"],"reach")
        with self.assertRaises(ContractError):
            PostgreSQLD1FactsReader().read_snapshot(
                _FakeDB({"FROM metric_snapshots WHERE id":original}),3,10)

    def test_history_is_exact_version_and_bounded(self):
        d=PostgreSQLD1FactsReader()
        for args in (
            (1,"s","views","v1",_NOW,0),
            (1,"s","views","v1",_NOW,65),
            (1,"s","views","v1",_NOW,True),
            (1,"s","","v1",_NOW,1),
            (1,"s","views","",_NOW,1),
            (1,"s","views","v1",datetime(2024,1,1),1),
        ):
            with self.subTest(args=args),self.assertRaises(ContractError):
                d.read_previous_snapshots(_FakeDB(),*args)
        db=_FakeDB({"FROM metric_snapshots": []})
        self.assertEqual(d.read_previous_snapshots(db,1,"src","views","v2",_NOW,5),[])
        sql,params=db.calls[-1]
        self.assertIn("source_metric_definition=%s",sql)
        self.assertIn("source_metric_version=%s",sql)
        self.assertIn("observed_at<%s",sql)
        self.assertIn("ORDER BY observed_at DESC,id DESC",sql)
        self.assertEqual(params,(1,"src","views","v2",_NOW,5))

    def test_approval_and_cohort_not_synthesized(self):
        src=PostgreSQLSourcePolicyReader()
        with self.assertRaises(ContractError) as e:
            src.assert_eligible(_FakeDB(),"src",1,[2],3,[5])
        self.assertEqual(e.exception.code,"cohort_approval_unavailable")
        with self.assertRaises(ContractError):
            src.read_effective_policy(_FakeDB(),"src",None)
        with self.assertRaises(ContractError):
            src.assert_eligible(_FakeDB(),"src",1,[2,2],3,[])
        with self.assertRaises(ContractError):
            src.assert_eligible(_FakeDB(),"src",1,[True],3,[])


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "requires disposable CM_TEST_PG_DSN and GC_TEST_DISPOSABLE_DB=YES",
)
class D1ReadersPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.conn=psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True)
        num=int(cls.conn.execute("SHOW server_version_num").fetchone()[0])
        if not (170000<=num<180000):
            raise AssertionError("Requires PG17 disposable database")
        migrate.apply_migrations(cls.conn)
        cls.d1=PostgreSQLD1FactsReader()
        cls.registry=PostgreSQLCreatorTopicRegistry()
        cls.policy_reader=PostgreSQLSourcePolicyReader()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.project=uuid4().hex
        self.src="gc-d1-"+uuid4().hex
        self.conn.execute("""INSERT INTO project_registry
          (id,name,profile_revision,profile_hash,created_at,updated_at)
          VALUES(%s,'Group C D1 fixture',1,%s,now(),now())""",
          (self.project,"f"*64))
        self.conn.execute("""INSERT INTO sre_workspaces(project_id) VALUES(%s)""",
                          (self.project,))
        self.conn.execute("""INSERT INTO sources
          (id,provider,source_type,status,can_store_metadata,can_store_metrics,
           can_derive_scores,retention_days)
          VALUES(%s,'gc-ci','manual','enabled',TRUE,TRUE,TRUE,30)""",(self.src,))
        self.policy=self.conn.execute("""INSERT INTO source_policy_versions
          (source_id,version,is_active,permissions,retention_policy)
          VALUES(%s,1,TRUE,%s::jsonb,'{"retention_days":30}'::jsonb)
          RETURNING id""",
          (self.src,'{"can_store_metadata":true,"can_store_metrics":true,"can_derive_scores":true}')
        ).fetchone()[0]
        self.creator=self.conn.execute("""INSERT INTO creators(platform,platform_creator_id)
          VALUES('youtube',%s) RETURNING id""",(uuid4().hex,)).fetchone()[0]
        token=uuid4().hex
        self.video=self.conn.execute("""INSERT INTO videos
          (platform,platform_video_id,creator_id,canonical_url,content_kind,c_content_type)
          VALUES('youtube',%s,%s,%s,'slideshow_video','video') RETURNING id""",
          (token,self.creator,"https://youtube.com/watch?v="+token)).fetchone()[0]
        self.conn.execute("""INSERT INTO sre_project_videos(project_id,video_id)
          VALUES(%s,%s)""",(self.project,self.video))
        self.meta=self.conn.execute("""INSERT INTO source_video_metadata
          (video_id,source_id,source_policy_version_id,fetched_at,published_at)
          VALUES(%s,%s,%s,now()-interval '2 hour',now()-interval '2 day')
          RETURNING id""",(self.video,self.src,self.policy)).fetchone()[0]
        self.snapshot=[]
        for hours,views in ((6,None),(1,25)):
            self.snapshot.append(self.conn.execute("""INSERT INTO metric_snapshots
              (video_id,source_id,source_policy_version_id,observed_at,
               views,likes,comments,shares,source_metric_definition,source_metric_version)
              VALUES(%s,%s,%s,now()-(%s*interval '1 hour'),%s,0,NULL,3,
                     'platform_post_views','v2') RETURNING id""",
              (self.video,self.src,self.policy,hours,views)).fetchone()[0])

    def tearDown(self):
        self.conn.execute("DELETE FROM sre_workspaces WHERE project_id=%s",(self.project,))
        self.conn.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
        self.conn.execute("DELETE FROM videos WHERE id=%s",(self.video,))
        self.conn.execute("DELETE FROM creators WHERE id=%s",(self.creator,))
        self.conn.execute("DELETE FROM sources WHERE id=%s",(self.src,))

    def test_schema_probes_and_project_scoped_registry(self):
        self.assertEqual(GroupCPorts(d1=self.d1,registry=self.registry,
                         source_policy=self.policy_reader).require_all(
                             ("d1","registry","source_policy"),self.conn).keys(),
                         {"d1","registry","source_policy"})
        r=self.registry.read_project_video(self.conn,self.project,self.video)
        self.assertEqual(r["creator_id"],self.creator)
        self.assertEqual(r["content_kind"],"slideshow_video")
        self.assertEqual(r["c_content_type"],"video")
        self.assertEqual(self.d1.read_video(self.conn,self.video)["creator_platform"],"youtube")
        with self.assertRaises(ContractError):
            self.registry.read_project_video(self.conn,uuid4().hex,self.video)

    def test_exact_publication_and_raw_null_counters(self):
        m=self.d1.read_publication(self.conn,self.video,self.meta)
        self.assertEqual(m["source_id"],self.src)
        a=self.d1.read_snapshot(self.conn,self.video,self.snapshot[0])
        self.assertIsNone(a["views"])
        self.assertIsNone(a["comments"])
        self.assertEqual(a["likes"],0)
        self.assertEqual(a["shares"],3)
        with self.assertRaises(ContractError):
            self.d1.read_snapshot(self.conn,self.video,9999999999999)
        earlier=self.d1.read_previous_snapshots(
            self.conn,self.video,self.src,"platform_post_views","v2",
            _NOW+timedelta(days=1),8)
        self.assertEqual([x["id"] for x in earlier],list(reversed(self.snapshot)))
        self.assertEqual(
            self.d1.read_previous_snapshots(self.conn,self.video,self.src,
                        "impressions","v2",_NOW+timedelta(days=1),8),[])

    def test_source_rights_metadata_and_revocation(self):
        ok=self.policy_reader.assert_eligible(
            self.conn,self.src,self.video,self.snapshot,self.meta,[])
        self.assertEqual(ok["state"],"source_eligible")
        self.assertFalse(ok["score_approved"])
        self.conn.execute("UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                          (self.src,))
        with self.assertRaises(ContractError):
            self.policy_reader.assert_eligible(
                self.conn,self.src,self.video,self.snapshot,self.meta,[])

    def test_quality_and_expiry_revocations(self):
        self.conn.execute("""UPDATE metric_snapshots
           SET data_quality_status='retracted' WHERE id=%s""",(self.snapshot[1],))
        with self.assertRaises(ContractError):
            self.policy_reader.assert_eligible(
                self.conn,self.src,self.video,self.snapshot,self.meta,[])
        self.conn.execute("""UPDATE source_video_metadata
          SET is_current=FALSE,deleted_at=now() WHERE id=%s""",(self.meta,))
        with self.assertRaises(ContractError):
            self.d1.read_publication(self.conn,self.video,self.meta)


if __name__ == "__main__":
    unittest.main()
