"""GB-MOD-19 optional disposable PostgreSQL17 B7 summary truth table.

Synthetic source and fake host/provider/full-media verifiers are TEST DOUBLES,
NOT production rights, signed scan, or paid inference.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
import json
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.blocks.b7_cta import B7CTAError,validate_b7_cta
from intelligence.group_b.blocks.b7_storage import persist_b7_cta
from test_group_b_b7 import media,context,candidate,response,Receipt,Full,Slide


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("B7 PG tests demand a disposable test database")
    return dsn


class Admission:
    def __init__(self,accepted=True):
        self.accepted=accepted
        self.calls=0
    def verify_cta_persistence(self,analysis,manifest,proposal):
        self.calls+=1
        return self.accepted is True and proposal.verify_digest() and analysis>0


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PG17 DSN")
class TestB7Postgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self,*,event=True,complete=True,attested=True,carousel=False):
        kind="photo_carousel" if carousel else "video"
        m=media(kind=kind,with_text=event and not carousel)
        source="b7-"+uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",
                (source,),
            )
            key=uuid4().hex
            video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,%s) RETURNING id""",
                (key,"https://example.test/b7/"+key,kind)
            ).fetchone()[0]
        m=replace(m,source_id=source,video_id=video,
                  source_policy_version_id=None)
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        p=context(m)
        raw=candidate(m,event=event,complete=complete,carousel=carousel)
        visual=tuple(x.ref_sha256 for x in m.observations
                     if x.kind in ("sampled_frame","slide_ref"))
        proposal=validate_b7_cta(
            manifest=m,packet=p,response=response(raw),
            receipt_verifier=Receipt(),delivered_image_refs=visual,
            full_scan_verifier=Full(attested),
            slide_quote_verifier=Slide(),
        )
        pins={
            "group_b_project_id":m.project_id,
            "group_b_run_id":m.b_run_id,
            "group_b_media_evidence_manifest_sha256":m.manifest_sha256,
            "group_b_b7_cta_proposal_sha256":proposal.proposal_sha256,
        }
        with self.psycopg.connect(self.dsn) as conn:
            analysis=conn.execute(
                """INSERT INTO content_b_analyses(
                      video_id,source_id,analysis_key,analysis_version,
                      content_type,method_name,method_version,state,method_details)
                   VALUES(%s,%s,%s,'v1',%s,'b7-fixture','1','pending',
                          %s::jsonb) RETURNING id""",
                (video,source,uuid4().hex,
                 "carousel" if carousel else "video",
                 json.dumps(pins,sort_keys=True))
            ).fetchone()[0]
        return analysis,m,proposal

    def test_sql005_true_one_detected_and_replay_denied(self):
        a,m,p=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            out=persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
        self.assertEqual(out,{"analysis_status":"complete",
                              "has_cta":True,"detected_occurrences":1})
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",(a,)
            ).fetchone()[0],"partial")
            with self.assertRaises(B7CTAError):
                persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())

    def test_sql005_zero_complete_is_false(self):
        a,m,p=self.prepare(event=False)
        with self.psycopg.connect(self.dsn) as conn:
            out=persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
        self.assertEqual(out,{"analysis_status":"complete",
                              "has_cta":False,"detected_occurrences":0})

    def test_sql005_zero_partial_is_unknown_not_false(self):
        a,m,p=self.prepare(event=False,attested=False)
        with self.psycopg.connect(self.dsn) as conn:
            out=persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
        self.assertEqual(out["analysis_status"],"partial")
        self.assertIsNone(out["has_cta"])
        self.assertEqual(out["detected_occurrences"],0)

    def test_sql005_positive_partial_still_unknown_summary(self):
        a,m,p=self.prepare(event=True,attested=False)
        with self.psycopg.connect(self.dsn) as conn:
            out=persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
        self.assertIsNone(out["has_cta"])
        self.assertEqual(out["detected_occurrences"],1)

    def test_carousel_slide_position_and_exact_count(self):
        a,m,p=self.prepare(event=True,carousel=True)
        with self.psycopg.connect(self.dsn) as conn:
            out=persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
        self.assertEqual(out["has_cta"],True)
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                """SELECT slide_index,start_sec,end_sec
                   FROM content_b_ctas WHERE analysis_id=%s""",(a,)
            ).fetchone()
        self.assertEqual(row,(1,None,None))

    def test_admission_or_changed_parent_pin_denies_entire_write(self):
        a,m,p=self.prepare()
        for gate in (None,Admission(False)):
            with self.psycopg.connect(self.dsn) as conn:
                with self.assertRaises(B7CTAError):
                    persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                                   admission_verifier=gate)
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE content_b_analyses SET method_details=jsonb_set(
                       method_details,'{group_b_b7_cta_proposal_sha256}',
                       to_jsonb('tampered'::text))
                   WHERE id=%s""",(a,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B7CTAError):
                persist_b7_cta(conn,analysis_id=a,manifest=m,proposal=p,
                               admission_verifier=Admission())
            self.assertFalse(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM content_b_cta_analyses WHERE analysis_id=%s)",
                (a,),
            ).fetchone()[0])


if __name__=="__main__":
    unittest.main()
