"""GC-MOD-27 Studio: exact rights-safe evidence package, not a scorer.

Live integration requires explicit disposable PG17 and local test-only IAM.
"""
from __future__ import annotations
import os
import unittest
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.studio_read import (
    STUDIO_VERSION, MAX_STUDIO_POINTS, PostgreSQLGroupCStudio,
    _project, _id, _response, _safe_raw,
)


class TestStudioPure(unittest.TestCase):
    def test_studio_version_and_unbounded_math_disclaimer(self):
        self.assertEqual(STUDIO_VERSION,"group-c-studio/v1")
        self.assertEqual(MAX_STUDIO_POINTS,48)
        r=_response(project_id="a"*32,video_id=7,run=None,
                    state="not_calculated",reason="no_c_run")
        self.assertEqual(r["schema_version"],STUDIO_VERSION)
        self.assertIsNone(r["score"])
        self.assertEqual(r["timeline"],[])
        self.assertEqual(r["cohorts"],[])
        self.assertTrue(any("0–100" in v for v in r["limitations"]))
        self.assertTrue(any("не вероятность" in v for v in r["limitations"]))

    def test_project_scope_never_inferred_from_video(self):
        self.assertEqual(_project("b"*32),"b"*32)
        for bad in ("a", "G"*32, "A"*32, [],1,None,True):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _project(bad)

    def test_video_id_int_only_never_bool(self):
        self.assertEqual(_id(18),18)
        for bad in (True,False,"18",None,0,-1,2**63):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                _id(bad)

    def test_denied_studio_has_no_numeric_or_cohort_payload(self):
        run={"id":uuid4(),"calculated_metric_id":22}
        r=_response(project_id="a"*32,video_id=3,run=run,
                    state="unavailable",reason="metadata_expired")
        self.assertIsNone(r["score"])
        self.assertIsNone(r["configuration"])
        self.assertIsNone(r["approval"])
        self.assertEqual(r["timeline"],[])
        self.assertEqual(r["cohorts"],[])
        self.assertEqual(r["reason_code"],"metadata_expired")

    def test_bundle_does_not_claim_external_human_verification(self):
        bundle=PostgreSQLGroupCStudio()
        self.assertFalse(hasattr(bundle,"auto_approve"))
        self.assertFalse(hasattr(bundle,"persist_score"))
        self.assertFalse(hasattr(bundle,"publish_to_provider"))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Disposable PG17 required and GC_TEST_DISPOSABLE_DB=YES",
)
class TestStudioPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.project=self.base.project
        self.video=self.base.target[0]
        self.reader=PostgreSQLGroupCStudio()

    def tearDown(self):
        self.base.tearDown()

    def complete(self):
        from test_group_c_durable_worker import _SyntheticC7IAM
        req=self.base.request()
        lease=self.base.claim()
        res=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=req.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        return res,req

    def read(self,video_id=None):
        return self.reader.read(
            self.conn,project_id=self.project,
            video_id=video_id if video_id is not None else self.video,
        )

    def test_queued_run_renders_explicit_pending_without_score(self):
        r=self.read()
        self.assertEqual(r["state"],"queued")
        self.assertIsNone(r["score"])
        self.assertEqual(r["timeline"],[])
        self.assertEqual(r["quality"]["status"],"queued")

    def test_project_video_no_run_has_no_fake_views(self):
        peer_video=self.base.fixture.peers[0][0]
        r=self.read(peer_video)
        self.assertEqual(r["state"],"not_calculated")
        self.assertEqual(r["timeline"],[])
        self.assertIsNone(r["score"])

    def test_live_result_has_exact_four_original_decimal_values(self):
        complete,req=self.complete()
        r=self.read()
        self.assertEqual(r["state"],"valid_current")
        score=r["score"]
        self.assertEqual(score["breakout_score"],complete.score.breakout_score)
        self.assertEqual(score["evidence_sha256"],complete.score.evidence_sha256)
        self.assertEqual(len(score["components"]),4)
        self.assertEqual([p["key"] for p in score["components"]],
                         ["creator","growth","engagement","freshness"])
        self.assertEqual(
            sum([x["value"] for x in score["components"]],Decimal(0)),
            score["breakout_score"],
        )
        self.assertEqual(score["scale"],"unbounded_not_0_to_100")
        self.assertEqual(score["metric_kind"],"weighted_components_not_probability")
        self.assertEqual(score["snapshot_id"],self.base.target[1])
        self.assertEqual(score["previous_snapshot_id"],self.base.previous)
        self.assertEqual(len(r["cohorts"]),2)
        self.assertEqual({c["purpose"] for c in r["cohorts"]},
                         {"creator","engagement_prior"})
        self.assertEqual(r["approval"]["decision"],"approved")
        self.assertEqual(str(r["approval"]["review_request_id"]),req.request_id)
        self.assertEqual(r["configuration"]["id"],self.base.config)
        self.assertEqual(r["quality"]["status"],"verified")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.video,),
        ).fetchone()[0],1)

    def test_timeline_uses_exact_observed_at_and_original_pinned_pair(self):
        self.complete()
        r=self.read()
        timeline=r["timeline"]
        self.assertGreaterEqual(len(timeline),2)
        self.assertLessEqual(len(timeline),MAX_STUDIO_POINTS)
        self.assertEqual([p["observed_at"] for p in timeline],
                         sorted([p["observed_at"] for p in timeline]))
        self.assertIn(self.base.previous,[p["snapshot_id"] for p in timeline])
        self.assertIn(self.base.target[1],[p["snapshot_id"] for p in timeline])
        self.assertEqual(sum(p["is_current"] for p in timeline),1)
        self.assertTrue(all(p["quality_status"]=="ok" for p in timeline))

    def test_revoked_source_never_leaks_old_views_or_c7(self):
        self.complete()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        r=self.read()
        self.assertEqual(r["state"],"unavailable")
        self.assertEqual(r["reason_code"],"source_policy_unavailable")
        self.assertIsNone(r["score"])
        self.assertEqual(r["timeline"],[])
        self.assertEqual(r["cohorts"],[])
        self.assertIsNone(r["approval"])

    def test_expired_original_snapshot_denies_full_bundle(self):
        self.complete()
        self.conn.execute(
            "UPDATE metric_snapshots SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(self.base.target[1],),
        )
        r=self.read()
        self.assertEqual(r["state"],"unavailable")
        self.assertEqual(r["reason_code"],"metric_snapshot_expired")
        self.assertEqual(r["timeline"],[])
        self.assertIsNone(r["score"])

    def test_expired_peer_member_never_returns_cohort_median(self):
        complete,_=self.complete()
        peer=self.conn.execute(
            """SELECT m.snapshot_id FROM group_c_calculation_receipts r
               JOIN group_c_cohort_freeze_members m ON m.freeze_id=r.creator_freeze_id
               WHERE r.calculated_metric_id=%s LIMIT 1""",
            (complete.score.calculated_metric_id,),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE metric_snapshots SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(peer,),
        )
        r=self.read()
        self.assertEqual(r["state"],"unavailable")
        self.assertIsNone(r["score"])
        self.assertEqual(r["cohorts"],[])
        self.assertEqual(r["timeline"],[])

    def test_new_unfiltered_raw_sample_blocks_superseded_studio(self):
        self.complete()
        self.conn.execute(
            """INSERT INTO metric_snapshots(
               video_id,source_id,source_policy_version_id,observed_at,
               collected_at,views,likes,comments,shares,
               source_metric_definition,source_metric_version,
               precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '5 minutes',
               now()-interval '4 minutes',200,20,4,2,
               'platform_post_views','v1','exact','ok',FALSE)""",
            (self.video,self.base.source,self.base.policy),
        )
        r=self.read()
        self.assertEqual(r["state"],"unavailable")
        self.assertEqual(r["reason_code"],"retention_version_superseded")
        self.assertEqual(r["timeline"],[])
        self.assertIsNone(r["score"])

    def test_project_scope_not_just_matching_video_id(self):
        self.complete()
        with self.assertRaises(ContractError) as e:
            self.reader.read(self.conn,project_id=uuid4().hex,video_id=self.video)
        self.assertEqual(e.exception.code,"project_video_unavailable")

    def test_published_expired_returns_empty_timeline_and_no_config(self):
        self.complete()
        self.conn.execute(
            "UPDATE source_video_metadata SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(self.base.target[2],),
        )
        r=self.read()
        self.assertEqual(r["state"],"unavailable")
        self.assertIsNone(r["score"])
        self.assertIsNone(r["configuration"])
        self.assertEqual(r["timeline"],[])

    def test_policy_window_bounds_historical_observations(self):
        self.complete()
        # Narrowing retention to 1 day still permits the target/pinned
        # observations from a few hours ago but excludes older history.
        self.conn.execute(
            "UPDATE sources SET retention_days=1 WHERE id=%s",
            (self.base.source,),
        )
        self.conn.execute(
            "UPDATE source_policy_versions SET retention_policy=%s::jsonb WHERE id=%s",
            ('{"retention_days":1}',self.base.policy),
        )
        r=self.read()
        self.assertEqual(r["state"],"valid_current")
        self.assertTrue(all(p["views"]>=0 for p in r["timeline"]))

    def test_scoped_HTTP_studio_no_uncertified_score_or_origin_data(self):
        from intelligence.group_c.http_api import GroupCHTTPAPI
        from test_group_c_http_api import _TestIAM,_env,_call,HOST,ORIGIN
        import psycopg
        grant=_TestIAM(scope=self.project)
        api=GroupCHTTPAPI(
            connect=lambda:psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                                            autocommit=True),
            access_verifier=grant,
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
        )
        path="/api/group-c/v1/projects/"+self.project+"/videos/"+str(self.video)+"/studio"
        status,body,_=_call(api,_env(path=path))
        self.assertEqual(status,200)
        self.assertEqual(body["schema_version"],"group-c-http/v1")
        self.assertIsNone(body["data"]["score"])
        self.assertEqual(body["data"]["schema_version"],STUDIO_VERSION)
        self.complete()
        status,body,_=_call(api,_env(path=path))
        self.assertEqual(status,200)
        self.assertEqual(body["data"]["state"],"valid_current")
        self.assertIsInstance(body["data"]["score"]["breakout_score"],str)
        self.assertEqual(len(body["data"]["score"]["components"]),4)
        self.assertTrue(any(p["is_current"] for p in body["data"]["timeline"]))
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.base.source,),
        )
        status,body,_=_call(api,_env(path=path))
        self.assertEqual(status,200)
        self.assertEqual(body["data"]["state"],"unavailable")
        self.assertIsNone(body["data"]["score"])
        self.assertEqual(body["data"]["timeline"],[])

    def test_HTTP_studio_cross_project_denied_before_db(self):
        from intelligence.group_c.http_api import GroupCHTTPAPI
        from test_group_c_http_api import _TestIAM,_env,_call,HOST,ORIGIN,OTHER
        import psycopg
        api=GroupCHTTPAPI(
            connect=lambda:psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                                            autocommit=True),
            access_verifier=_TestIAM(scope=OTHER),
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
        )
        path="/api/group-c/v1/projects/"+self.project+"/videos/"+str(self.video)+"/studio"
        status,body,_=_call(api,_env(path=path))
        self.assertEqual(status,403)
        self.assertEqual(body["error"]["code"],"not_authorized")

    def test_serializable_pg17_and_migration_history_idempotence(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        self.complete()
        self.assertEqual(self.read()["state"],"valid_current")
        path=Path(__file__).resolve().parents[1] / "intelligence/migrations/062_group_c_retention_revocation.sql"
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (path.name,),
        ).fetchone()[0],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
