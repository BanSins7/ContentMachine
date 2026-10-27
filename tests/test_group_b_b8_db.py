"""GB-MOD-20 optional disposable PostgreSQL17 B8 assignment + stale registry tests.

Fake provider and fresh admission are TEST DOUBLES, not authorization for
production database/media/model usage.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
import json
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.topic_registry import read_topic_registry
from intelligence.group_b.blocks.b8_topic import (
    B8TopicError,validate_b8_topic,
)
from intelligence.group_b.blocks.b8_storage import persist_b8_topic
from test_group_b_b8 import source,context,output,response,Receipt


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    name=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in name and "_ci" not in name:
        raise RuntimeError("B8 PG17 tests only run on disposable test database")
    return dsn


class Host:
    def __init__(self,accepted=True):
        self.accepted=accepted
        self.calls=0

    def verify_topic_persistence(self,analysis_id,manifest,registry,proposal):
        self.calls+=1
        return self.accepted is True and analysis_id>0 and registry.verify_digest() and proposal.verify_digest()


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL DSN")
class TestB8PG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self,*,carousel=False,merged_topic=False,new_topic=False):
        m=source(kind="photo_carousel" if carousel else "video")
        source_id="b8-"+uuid4().hex
        stem=uuid4().hex[:14]
        cat_name="B8 Category "+stem
        top_name="B8 Topic "+stem
        sub_name="B8 Subtopic "+stem
        with self.psycopg.connect(self.dsn) as conn:
            category_id=conn.execute(
                """INSERT INTO content_b_categories(slug,name)
                   VALUES(%s,%s) RETURNING id""",
                ("b8category_"+stem,cat_name)
            ).fetchone()[0]
            topic_id=conn.execute(
                """INSERT INTO content_b_topics(category_id,slug,name)
                   VALUES(%s,%s,%s) RETURNING id""",
                (category_id,"b8topic_"+stem,top_name)
            ).fetchone()[0]
            sub_id=conn.execute(
                """INSERT INTO content_b_subtopics(topic_id,slug,name)
                   VALUES(%s,%s,%s) RETURNING id""",
                (topic_id,"b8subtopic_"+stem,sub_name)
            ).fetchone()[0]
            if merged_topic:
                alias_name="B8 Old Topic "+stem
                conn.execute(
                    """INSERT INTO content_b_topics(
                          category_id,slug,name,merged_into_id)
                       VALUES(%s,%s,%s,%s)""",
                    (category_id,"b8oldtopic_"+stem,alias_name,topic_id)
                )
                top_name=alias_name
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",
                (source_id,)
            )
            video_key=uuid4().hex
            video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,
                                      canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,%s) RETURNING id""",
                (video_key,"https://example.test/b8/"+video_key,m.content_kind)
            ).fetchone()[0]
        m=replace(m,source_id=source_id,video_id=video,
                  source_policy_version_id=None)
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        p=context(m)
        with self.psycopg.connect(self.dsn) as conn:
            registry=read_topic_registry(conn)
        raw=output(
            m,category=cat_name,
            topic=top_name if not new_topic else "Unapproved Topic "+stem,
            subtopic=sub_name if not new_topic else "",
        )
        image_refs=tuple(o.ref_sha256 for o in m.observations
                         if o.kind in ("sampled_frame","slide_ref"))
        proposal=validate_b8_topic(
            manifest=m,packet=p,registry=registry,
            response=response(raw),receipt_verifier=Receipt(),
            delivered_image_refs=image_refs,
        )
        pins={
            "group_b_project_id":m.project_id,
            "group_b_run_id":m.b_run_id,
            "group_b_media_evidence_manifest_sha256":m.manifest_sha256,
            "group_b_b8_registry_snapshot_sha256":registry.snapshot_sha256,
            "group_b_b8_topic_proposal_sha256":proposal.proposal_sha256,
        }
        with self.psycopg.connect(self.dsn) as conn:
            a=conn.execute(
                """INSERT INTO content_b_analyses(
                      video_id,source_id,analysis_key,analysis_version,
                      content_type,method_name,method_version,state,method_details)
                   VALUES(%s,%s,%s,'v1',%s,'b8-fixture','1','pending',
                          %s::jsonb) RETURNING id""",
                (video,source_id,uuid4().hex,
                 "carousel" if carousel else "video",
                 json.dumps(pins,sort_keys=True))
            ).fetchone()[0]
        return a,m,registry,proposal,category_id,topic_id,sub_id

    def test_exact_existing_ids_one_shot_and_parent_partial(self):
        a,m,reg,p,cat,top,sub=self.prepare()
        self.assertTrue(p.can_persist)
        with self.psycopg.connect(self.dsn) as conn:
            saved=persist_b8_topic(conn,analysis_id=a,manifest=m,
                                   registry=reg,proposal=p,admission_verifier=Host())
        self.assertEqual(saved,{
            "category_id":cat,"topic_id":top,"subtopic_id":sub,
            "taxonomy_version":"1.0","needs_review":False,
        })
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",(a,)
            ).fetchone()[0],"partial")
            with self.assertRaises(B8TopicError):
                persist_b8_topic(conn,analysis_id=a,manifest=m,
                                 registry=reg,proposal=p,admission_verifier=Host())

    def test_category_topic_without_subtopic_and_carousel(self):
        a,m,reg,p,cat,top,sub=self.prepare(carousel=True)
        # A subtopic is optional in SQL005; our positive full path also
        # establishes that carousel and video B8 share one assignment table.
        with self.psycopg.connect(self.dsn) as conn:
            saved=persist_b8_topic(conn,analysis_id=a,manifest=m,
                                   registry=reg,proposal=p,admission_verifier=Host())
        self.assertEqual(saved["category_id"],cat)
        self.assertEqual(saved["topic_id"],top)
        self.assertEqual(saved["subtopic_id"],sub)

    def test_merged_alias_maps_only_to_approved_current_id(self):
        a,m,reg,p,cat,top,sub=self.prepare(merged_topic=True)
        self.assertTrue(p.can_persist)
        self.assertEqual(p.payload["alias_levels"],["topic"])
        with self.psycopg.connect(self.dsn) as conn:
            saved=persist_b8_topic(conn,analysis_id=a,manifest=m,
                                   registry=reg,proposal=p,admission_verifier=Host())
        self.assertEqual(saved["topic_id"],top)

    def test_new_semantic_proposal_cannot_write_taxonomy_or_assignment(self):
        a,m,reg,p,*_=self.prepare(new_topic=True)
        self.assertFalse(p.can_persist)
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B8TopicError):
                persist_b8_topic(conn,analysis_id=a,manifest=m,
                                 registry=reg,proposal=p,admission_verifier=Host())
            self.assertFalse(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM content_b_topic_assignments WHERE analysis_id=%s)",
                (a,)
            ).fetchone()[0])

    def test_stale_registry_snapshot_before_save_rejected(self):
        a,m,reg,p,*_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            stem=uuid4().hex
            conn.execute(
                """INSERT INTO content_b_categories(slug,name)
                   VALUES(%s,%s)""",
                ("b8external_"+stem,"B8 New Category "+stem)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B8TopicError):
                persist_b8_topic(conn,analysis_id=a,manifest=m,
                                 registry=reg,proposal=p,admission_verifier=Host())
            self.assertFalse(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM content_b_topic_assignments WHERE analysis_id=%s)",
                (a,)
            ).fetchone()[0])

    def test_missing_fresh_host_and_wrong_project_pin(self):
        a,m,reg,p,*_=self.prepare()
        for gate in (None,Host(False)):
            with self.psycopg.connect(self.dsn) as conn:
                with self.assertRaises(B8TopicError):
                    persist_b8_topic(conn,analysis_id=a,manifest=m,
                                     registry=reg,proposal=p,admission_verifier=gate)
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE content_b_analyses SET method_details=jsonb_set(
                      method_details,'{group_b_b8_topic_proposal_sha256}',
                      to_jsonb('spoofed'::text))
                   WHERE id=%s""",(a,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B8TopicError):
                persist_b8_topic(conn,analysis_id=a,manifest=m,
                                 registry=reg,proposal=p,admission_verifier=Host())


if __name__=="__main__":
    unittest.main()
