"""UA-MOD-30 disposable PostgreSQL 17 VIDEO + CAROUSEL E2E.

Real migrations + transactional UA14/15 snapshot repository, source rights,
immutable seven-row evidence, duplicate request replay, cross-project SQL
and UA85 protection. Actual peer-approved Group C cannot be fabricated:
the independently trusted QDP/SRE/A/B/C producer chain is not integrated.
Set ONLY disposable CM_TEST_PG_DSN (different from CM_DB_DSN).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import os
from uuid import uuid4
import unittest

from intelligence.unified_analytics import InputScope
from intelligence.unified_analytics.snapshot_assembly import (
    ImmutableSnapshotRepository,
)
from test_unified_analytics_transactional_persistence import (
    TransactionalPersistencePG17,UnapprovedSyntheticAssembler,
)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL 17 CM_TEST_PG_DSN required")
class FullFixturePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.f=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.f.setUp()
        self.extra_videos=[]

    def tearDown(self):
        # Existing UA15 fixture cleans only its original video.
        self.f.tearDown()
        if self.extra_videos:
            with self.f.connect() as db:
                with db.transaction():
                    for video in self.extra_videos:
                        db.execute("DELETE FROM videos WHERE id=%s",(video,))

    def carousel(self,kind="photo_carousel"):
        f=self.f
        with f.connect() as db:
            with db.transaction():
                video=db.execute(
                    "INSERT INTO videos("
                    "platform,platform_video_id,canonical_url,content_kind)"
                    " VALUES('youtube',%s,%s,%s) RETURNING id",
                    (uuid4().hex,"https://example.invalid/uae30-carousel",
                     kind),
                ).fetchone()["id"]
                self.extra_videos.append(video)
                db.execute(
                    "INSERT INTO sre_project_videos(project_id,video_id)"
                    " VALUES(%s,%s)",(f.project,video)
                )
                request=db.execute(
                    "INSERT INTO uae_qualification_requests("
                    "project_id,video_id,source_id,source_policy_version_id,"
                    "qdp_run_id,qdp_input_sha256,content_kind,"
                    "request_key,request_sha256)"
                    " VALUES(%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s)"
                    " RETURNING id",
                    (f.project,video,f.source,f.policy,f.run,f.sha,kind,
                     "ua30.carousel."+uuid4().hex,f.sha),
                ).fetchone()["id"]
        scope=InputScope(
            f.project,video,f.source,f.policy,kind,f.run,f.sha,
        )
        return scope,str(request)

    def persist(self,scope,request,*,when=None):
        with self.f.connect() as db:
            asm=UnapprovedSyntheticAssembler(
                db,scope,self.f.as_of if when is None else when,
            )
            return ImmutableSnapshotRepository(
                asm,request,
            ).assemble_and_save()

    def assert_private_rows(self,sid,kind):
        f=self.f
        with f.connect() as db:
            sn=db.execute(
                "SELECT content_kind,content_type,status,"
                "inputs_sha256,decision_sha256,snapshot_version "
                "FROM uae_unified_snapshots WHERE project_id=%s AND id=%s",
                (f.project,sid),
            ).fetchone()
            self.assertIsNotNone(sn)
            self.assertEqual(sn["content_kind"],kind)
            self.assertEqual(sn["content_type"],
                             "carousel" if "carousel" in kind else "video")
            self.assertIn(sn["status"],("partial","blocked"))
            self.assertEqual(len(sn["inputs_sha256"]),64)
            self.assertEqual(len(sn["decision_sha256"]),64)
            ev=db.execute(
                "SELECT port,state,checked,is_current FROM uae_snapshot_evidence "
                "WHERE project_id=%s AND snapshot_id=%s ORDER BY port",
                (f.project,sid),
            ).fetchall()
            self.assertEqual(len(ev),7)
            self.assertTrue(all(
                x["state"] in ("partial","missing","unavailable")
                and x["checked"] is False and x["is_current"] is False
                for x in ev
            ))
            payload=db.execute(
                "SELECT payload,decision_sha256,payload_sha256 "
                "FROM uae_snapshot_payloads WHERE project_id=%s AND snapshot_id=%s",
                (f.project,sid),
            ).fetchone()
            self.assertIsNotNone(payload)
            self.assertEqual(payload["payload"]["status"],sn["status"])
            self.assertEqual(payload["decision_sha256"],sn["decision_sha256"])
            self.assertEqual(len(payload["payload_sha256"]),64)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_snapshot_current_rights_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",(f.project,),
            ).fetchone()["n"],0)

    def test_video_private_seven_port_snapshot_golden(self):
        receipt=self.f.save()
        self.assertEqual(receipt.snapshot_version,1)
        self.assertFalse(receipt.qualified)
        self.assert_private_rows(receipt.snapshot_id,"video")

    def test_photo_carousel_private_seven_port_snapshot_golden(self):
        scope,rid=self.carousel("photo_carousel")
        receipt=self.persist(scope,rid)
        self.assertEqual(receipt.snapshot_version,1)
        self.assertFalse(receipt.qualified)
        self.assert_private_rows(receipt.snapshot_id,"photo_carousel")

    def test_mixed_carousel_private_seven_port_snapshot_golden(self):
        scope,rid=self.carousel("mixed_carousel")
        receipt=self.persist(scope,rid)
        self.assertFalse(receipt.qualified)
        self.assert_private_rows(receipt.snapshot_id,"mixed_carousel")

    def test_carousel_replay_same_version_and_digests(self):
        scope,rid=self.carousel("photo_carousel")
        first=self.persist(scope,rid)
        second=self.persist(scope,rid)
        self.assertEqual(first.snapshot_id,second.snapshot_id)
        self.assertEqual(first.payload_sha256,second.payload_sha256)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assert_private_rows(first.snapshot_id,"photo_carousel")

    def test_parallel_video_request_deduplicates_exactly(self):
        f=self.f
        def work(_):
            with f.connect() as db:
                return f.save(db=db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=list(pool.map(work,(1,2)))
        self.assertEqual(a.snapshot_id,b.snapshot_id)
        self.assertEqual(sorted((a.replayed,b.replayed)),[False,True])
        self.assertEqual(f.count("uae_unified_snapshots"),1)

    def test_video_and_carousel_same_source_no_version_or_row_leak(self):
        f=self.f
        first=f.save()
        scope,rid=self.carousel("photo_carousel")
        second=self.persist(scope,rid)
        self.assertNotEqual(first.snapshot_id,second.snapshot_id)
        self.assertEqual((first.snapshot_version,second.snapshot_version),(1,1))
        self.assertNotEqual(first.scope_sha256,second.scope_sha256)
        with f.connect() as db:
            counts=db.execute(
                "SELECT content_kind,count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s GROUP BY content_kind ORDER BY content_kind",
                (f.project,),
            ).fetchall()
            self.assertEqual(
                {r["content_kind"]:r["n"] for r in counts},
                {"video":1,"photo_carousel":1},
            )
        self.assert_private_rows(first.snapshot_id,"video")
        self.assert_private_rows(second.snapshot_id,"photo_carousel")

    def test_cross_project_snapshot_and_evidence_cannot_be_read_with_own_id(self):
        f=self.f
        receipt=f.save()
        other="a"*32 if f.project!="a"*32 else "b"*32
        with f.connect() as db:
            for table,col in (("uae_unified_snapshots","id"),
                              ("uae_snapshot_evidence","snapshot_id"),
                              ("uae_snapshot_payloads","snapshot_id")):
                value=db.execute(
                    "SELECT count(*) AS n FROM "+table+
                    " WHERE project_id=%s AND "+col+"=%s",
                    (other,receipt.snapshot_id),
                ).fetchone()["n"]
                self.assertEqual(value,0,table)

    def test_source_permission_revoke_cannot_publish_prior_snapshot(self):
        f=self.f
        snap=f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute("UPDATE sources SET can_derive_scores=FALSE "
                           "WHERE id=%s",(f.source,))
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT status FROM uae_unified_snapshots "
                "WHERE project_id=%s AND id=%s",
                (f.project,snap.snapshot_id)
            ).fetchone()["status"],"partial")

    def test_085_blocks_direct_qualified_snapshot_insert(self):
        f=self.f
        receipt=f.save()
        with f.connect() as db:
            with self.assertRaises(Exception) as caught:
                with db.transaction():
                    db.execute(
                        "INSERT INTO uae_unified_snapshots("
                        "project_id,request_id,video_id,source_id,"
                        "source_policy_version_id,qdp_run_id,qdp_input_sha256,"
                        "content_kind,content_type,snapshot_version,"
                        "status,scope_sha256,inputs_sha256,decision_sha256,"
                        "blockers,readiness,verified_as_of)"
                        " SELECT project_id,request_id,video_id,source_id,"
                        "source_policy_version_id,qdp_run_id,qdp_input_sha256,"
                        "content_kind,content_type,snapshot_version+1,"
                        "'qualified',scope_sha256,inputs_sha256,decision_sha256,"
                        "blockers,readiness,verified_as_of "
                        "FROM uae_unified_snapshots WHERE project_id=%s AND id=%s",
                        (f.project,receipt.snapshot_id),
                    )
            self.assertEqual(getattr(caught.exception,"sqlstate",None),"23514")
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",(f.project,),
            ).fetchone()["n"],0)

    def test_085_blocks_forged_complete_or_checked_evidence(self):
        f=self.f
        receipt=f.save()
        with f.connect() as db:
            with self.assertRaises(Exception) as caught:
                with db.transaction():
                    db.execute(
                        "INSERT INTO uae_snapshot_evidence("
                        "project_id,snapshot_id,port,state,checked,is_current,"
                        "record_key,producer_schema,producer_version,"
                        "evidence_sha256,lineage_sha256,reason_code)"
                        " SELECT project_id,snapshot_id,port,'complete',TRUE,TRUE,"
                        "record_key,producer_schema,producer_version,"
                        "evidence_sha256,lineage_sha256,reason_code "
                        "FROM uae_snapshot_evidence "
                        "WHERE project_id=%s AND snapshot_id=%s LIMIT 1",
                        (f.project,receipt.snapshot_id),
                    )
            self.assertEqual(getattr(caught.exception,"sqlstate",None),"23514")
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_snapshot_evidence "
                "WHERE project_id=%s AND checked IS TRUE",(f.project,),
            ).fetchone()["n"],0)

    def test_immutable_evidence_member_delete_rolls_back(self):
        f=self.f
        receipt=f.save()
        with f.connect() as db:
            with self.assertRaises(Exception):
                with db.transaction():
                    db.execute(
                        "DELETE FROM uae_snapshot_evidence "
                        "WHERE project_id=%s AND snapshot_id=%s AND port='group_c'",
                        (f.project,receipt.snapshot_id),
                    )
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_snapshot_evidence "
                "WHERE project_id=%s AND snapshot_id=%s",
                (f.project,receipt.snapshot_id),
            ).fetchone()["n"],7)

    def test_qualified_publishing_stays_zero_even_two_media_kinds(self):
        f=self.f
        f.save()
        scope,rid=self.carousel("mixed_carousel")
        self.persist(scope,rid)
        with f.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_project_aggregate_runs "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)


if __name__=="__main__":
    unittest.main()
