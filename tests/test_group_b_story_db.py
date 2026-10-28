"""GB-MOD-16 real disposable PG17 SQL005 B3 story pattern and rollback QA.

Precreated, explicitly pinned partial parent B analysis only; verifier is
FAKE in tests. There is NO live QDP/GA provider receipt or paid execution.
"""
from __future__ import annotations
from dataclasses import replace
from uuid import uuid4
import json
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.blocks.b3_sampling import build_b3_sampling_plan
from intelligence.group_b.blocks.b3_story import validate_b3_story,B3StoryError
from intelligence.group_b.blocks.b3_storage import persist_b3_story
from test_group_b_story import (
    make_video,make_carousel,reply,FakeReceipt,FakeBoundary,
)


def safe_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("Refusing non-disposable PG17 DB")
    return dsn


class HostGate:
    def __init__(self,allow=True):
        self.allow=allow
        self.calls=0
    def verify_story_persistence(self,analysis_id,manifest,plan,proposal):
        self.calls+=1
        return self.allow is True and analysis_id>0 and plan.verify_digest()


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL 17 DSN missing")
class TestStoryPersistencePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=safe_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def fixture(self,*,carousel=False):
        m,p,refs,raw=make_carousel() if carousel else make_video()
        source="b3-story-"+uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",
                (source,)
            )
            key=uuid4().hex
            video_id=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,%s) RETURNING id""",
                (key,"https://example.test/story/"+key,m.content_kind)
            ).fetchone()[0]
        # This DB fixture has no live policy-version producer. A real
        # integration must verify the currently active source policy.
        m=replace(m,video_id=video_id,source_id=source,
                  source_policy_version_id=None)
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        p=build_b3_sampling_plan(m)
        refs=tuple(p.matched_refs)
        # Rebuild candidate using the new exact slot refs (same original A
        # fixture refs), no raw source bytes/model requests involved.
        proposal=validate_b3_story(
            manifest=m,plan=p,response=reply(raw),
            receipt_verifier=FakeReceipt(refs),
            delivered_visual_refs=refs,
            boundary_verifier=FakeBoundary()
        )
        pins={
            "group_b_project_id":m.project_id,
            "group_b_run_id":m.b_run_id,
            "group_b_media_evidence_manifest_sha256":m.manifest_sha256,
            "group_b_b3_sampling_plan_sha256":p.plan_sha256,
            "group_b_b3_story_proposal_sha256":proposal.proposal_sha256,
        }
        with self.psycopg.connect(self.dsn) as conn:
            analysis=conn.execute(
                """INSERT INTO content_b_analyses(
                       video_id,source_id,analysis_key,analysis_version,
                       content_type,method_name,method_version,state,
                       method_details)
                   VALUES(%s,%s,%s,'v1',%s,'b3-test-gate','1','pending',%s::jsonb)
                   RETURNING id""",
                (video_id,source,uuid4().hex,proposal.payload["content_type"],
                 json.dumps(pins,sort_keys=True))
            ).fetchone()[0]
        return analysis,m,p,proposal

    def test_sql005_video_intervals_view_preserves_repeated_roles(self):
        analysis,m,p,proposal=self.fixture()
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                saved=persist_b3_story(
                    conn,analysis_id=analysis,manifest=m,plan=p,
                    proposal=proposal,admission_verifier=HostGate())
        self.assertEqual(saved,("before_after","before_after"))
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(tuple(conn.execute(
                "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0]),saved)
            self.assertEqual(conn.execute(
                """SELECT state,needs_review FROM content_b_analyses WHERE id=%s""",
                (analysis,)
            ).fetchone(),("partial",False))
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_frames WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],5)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_intervals WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],2)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_slides WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],0)
            metadata=conn.execute(
                "SELECT sampling_notes FROM content_b_story_sampling WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0]
            self.assertEqual(metadata["story_proposal_sha256"],proposal.proposal_sha256)
            self.assertTrue(metadata["source_data_not_model_delivered"])

    def test_sql005_carousel_view_repeated_role_order(self):
        analysis,m,p,proposal=self.fixture(carousel=True)
        with self.psycopg.connect(self.dsn) as conn:
            with conn.transaction():
                saved=persist_b3_story(
                    conn,analysis_id=analysis,manifest=m,plan=p,
                    proposal=proposal,admission_verifier=HostGate())
        self.assertEqual(saved,("context","context","cta"))
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(tuple(conn.execute(
                "SELECT story_pattern FROM content_b_story_patterns WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0]),saved)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_slides WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],3)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_intervals WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],0)

    def test_no_trusted_admission_or_changed_pin_never_mutates(self):
        analysis,m,p,proposal=self.fixture()
        for admission in (None,HostGate(False)):
            with self.psycopg.connect(self.dsn) as conn:
                with self.assertRaises(B3StoryError):
                    with conn.transaction():
                        persist_b3_story(
                            conn,analysis_id=analysis,manifest=m,plan=p,
                            proposal=proposal,admission_verifier=admission)
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE content_b_analyses
                   SET method_details=jsonb_set(method_details,
                     '{group_b_b3_story_proposal_sha256}',to_jsonb('tampered'::text))
                   WHERE id=%s""",(analysis,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B3StoryError):
                with conn.transaction():
                    persist_b3_story(
                        conn,analysis_id=analysis,manifest=m,plan=p,
                        proposal=proposal,admission_verifier=HostGate())
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_sampling WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],0)

    def test_one_shot_replay_denied_and_completed_parent_immutable(self):
        analysis,m,p,proposal=self.fixture()
        with self.psycopg.connect(self.dsn) as conn:
            persist_b3_story(conn,analysis_id=analysis,manifest=m,plan=p,
                             proposal=proposal,admission_verifier=HostGate())
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B3StoryError):
                with conn.transaction():
                    persist_b3_story(conn,analysis_id=analysis,manifest=m,plan=p,
                                     proposal=proposal,admission_verifier=HostGate())
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_sampling WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],1)
        # Tests do not mark an analysis 'completed' without all B1-B8; the
        # production trigger separately forbids overwrite of such analyses.

    def test_unique_conflict_rolls_back_sampling_and_parent_state(self):
        analysis,m,p,proposal=self.fixture()
        with self.psycopg.connect(self.dsn) as conn:
            # Force a SQL PK collision AFTER inserting sampling metadata.
            conn.execute(
                """INSERT INTO content_b_story_frames(
                     analysis_id,frame_index,time_sec,sampled_frame_ref,
                     role,needs_review)
                   VALUES(%s,1,0,%s,'hook',FALSE)""",
                (analysis,p.matched_refs[0])
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    persist_b3_story(conn,analysis_id=analysis,manifest=m,plan=p,
                                     proposal=proposal,admission_verifier=HostGate())
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM content_b_story_sampling WHERE analysis_id=%s",
                (analysis,)
            ).fetchone()[0],0)
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",
                (analysis,)
            ).fetchone()[0],"pending")


if __name__=="__main__":
    unittest.main()
