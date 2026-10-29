"""GB-MOD-17 optional disposable PostgreSQL 17 B4 one-shot storage tests.

All model/media/host admission objects are SYNTHETIC DOUBLES; no actual paid
inference, authentic whole-media absence or production policy read occurs.
"""
from __future__ import annotations
from dataclasses import replace
from uuid import uuid4
import json
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.blocks.b4_visual import (
    B4VisualError,validate_b4_visual,unknown_b4,
)
from intelligence.group_b.blocks.b4_storage import persist_b4_visual
from test_group_b_b4 import (
    packet,labels,Receipt,answer,Origin,WholeMedia,
)
from test_group_b_b2 import fixture


def safe_dsn():
    from psycopg.conninfo import conninfo_to_dict
    value=os.environ["CM_TEST_PG_DSN"]
    name=conninfo_to_dict(value).get("dbname","").lower()
    if "test" not in name and "_ci" not in name:
        raise RuntimeError("Refusing PostgreSQL outside disposable CI/test database")
    return value


class Gate:
    def __init__(self,accept=True):
        self.accept=accept
        self.calls=0
    def verify_visual_persistence(self,analysis,manifest,proposal):
        self.calls+=1
        return self.accept is True and analysis>0 and proposal.verify_digest()


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL DSN")
class TestB4StoragePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=safe_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self,*,carousel=False,with_origin=False):
        m=fixture(content_kind="photo_carousel" if carousel else "video")
        source="b4-source-"+uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",(source,)
            )
            key=uuid4().hex
            video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,
                                      content_kind)
                   VALUES('tiktok',%s,%s,%s) RETURNING id""",
                (key,"https://example.test/b4/"+key,m.content_kind)
            ).fetchone()[0]
        # Test setup uses source-policy-version NULL because no producer
        # exists here; runtime requires host fresh rights independently.
        m=replace(m,source_id=source,video_id=video,
                  source_policy_version_id=None)
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        refs=tuple(x.ref_sha256 for x in m.observations)
        p=packet(m)
        raw=labels(refs)
        result=validate_b4_visual(
            manifest=m,packet=p,response=answer(raw),
            delivered_image_refs=refs,receipt_verifier=Receipt(refs),
            origin_verifier=(
                Origin("generated","signed_generation_provenance")
                if with_origin else None
            )
        )
        self.assertTrue(result.can_persist)
        pins={
            "group_b_project_id":m.project_id,
            "group_b_run_id":m.b_run_id,
            "group_b_media_evidence_manifest_sha256":m.manifest_sha256,
            "group_b_b4_visual_proposal_sha256":result.proposal_sha256,
        }
        with self.psycopg.connect(self.dsn) as conn:
            analysis=conn.execute(
                """INSERT INTO content_b_analyses(
                      video_id,source_id,analysis_key,analysis_version,
                      content_type,method_name,method_version,state,
                      method_details)
                   VALUES(%s,%s,%s,'v1',%s,'b4-fixture','1','pending',
                          %s::jsonb) RETURNING id""",
                (video,source,uuid4().hex,"carousel" if carousel else "video",
                 json.dumps(pins,sort_keys=True))
            ).fetchone()[0]
        return analysis,m,result

    def test_exact_four_bools_and_nullable_ai_origin_sql005(self):
        a,m,proposal=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                saved=persist_b4_visual(
                    conn,analysis_id=a,manifest=m,proposal=proposal,
                    admission_verifier=Gate()
                )
        self.assertEqual(saved,{
            "ai_generated":None,
            "person_visible":True,
            "product_visible":True,
            "logo_visible":True,
        })
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                """SELECT ai_generated,person_visible,product_visible,logo_visible
                   FROM content_b_visual_content WHERE analysis_id=%s""",
                (a,)
            ).fetchone()
            self.assertEqual(row,(None,True,True,True))
            state=conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",(a,)
            ).fetchone()[0]
            self.assertEqual(state,"partial")

    def test_verified_ai_origin_and_carousel_write(self):
        a,m,proposal=self.prepare(carousel=True,with_origin=True)
        with self.psycopg.connect(self.dsn) as conn:
            stored=persist_b4_visual(
                conn,analysis_id=a,manifest=m,proposal=proposal,
                admission_verifier=Gate()
            )
        self.assertIs(stored["ai_generated"],True)
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT ai_generated FROM content_b_visual_content WHERE analysis_id=%s",
                    (a,)
                ).fetchone()[0],True
            )

    def test_missing_trusted_admission_or_changed_pin_no_write(self):
        a,m,proposal=self.prepare()
        for gate in (None,Gate(False)):
            with self.psycopg.connect(self.dsn) as conn:
                with self.assertRaises(B4VisualError):
                    with conn.transaction():
                        persist_b4_visual(
                            conn,analysis_id=a,manifest=m,
                            proposal=proposal,admission_verifier=gate
                        )
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE content_b_analyses
                   SET method_details=jsonb_set(
                       method_details,'{group_b_b4_visual_proposal_sha256}',
                       to_jsonb('tampered'::text))
                   WHERE id=%s""",(a,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B4VisualError):
                with conn.transaction():
                    persist_b4_visual(
                        conn,analysis_id=a,manifest=m,proposal=proposal,
                        admission_verifier=Gate()
                    )
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_visual_content WHERE analysis_id=%s",
                (a,)
            ).fetchone()[0],0)

    def test_unknown_or_partial_visual_never_inserted_as_false(self):
        a,m,_=self.prepare()
        partial=unknown_b4(manifest=m,reason="no_complete_visual_pass")
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B4VisualError):
                with conn.transaction():
                    persist_b4_visual(
                        conn,analysis_id=a,manifest=m,proposal=partial,
                        admission_verifier=Gate()
                    )
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_visual_content WHERE analysis_id=%s",
                (a,)
            ).fetchone()[0],0)

    def test_duplicate_b4_save_requires_new_analysis_key(self):
        a,m,proposal=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            persist_b4_visual(conn,analysis_id=a,manifest=m,
                              proposal=proposal,admission_verifier=Gate())
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B4VisualError):
                with conn.transaction():
                    persist_b4_visual(conn,analysis_id=a,manifest=m,
                                      proposal=proposal,admission_verifier=Gate())
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_visual_content WHERE analysis_id=%s",
                (a,)
            ).fetchone()[0],1)

    def test_sql_conflict_rolls_back_insertion_and_parent_state(self):
        a,m,proposal=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            # Existing B4 row is separately locked by one-shot check.
            # A SQL conflict must never promote analysis to completed.
            conn.execute(
                """INSERT INTO content_b_visual_content (
                   analysis_id,ai_generated,person_visible,product_visible,logo_visible)
                   VALUES (%s,NULL,TRUE,TRUE,TRUE)""",(a,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B4VisualError):
                with conn.transaction():
                    persist_b4_visual(
                        conn,analysis_id=a,manifest=m,proposal=proposal,
                        admission_verifier=Gate()
                    )
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",(a,)
            ).fetchone()[0],"pending")


if __name__=="__main__":
    unittest.main()
