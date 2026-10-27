"""GB-MOD-18 optional disposable PG17 one-shot subtitle SQL005 tests.

Fake host and detector acknowledgements are NOT a production media scan.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
import json
import copy
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.blocks.b6_subtitles import (
    B6SubtitleError, validate_b6_subtitles, unknown_b6,
)
from intelligence.group_b.blocks.b6_storage import persist_b6_subtitles
from test_group_b_b6 import scene, scan, Detector


def safe_dsn():
    from psycopg.conninfo import conninfo_to_dict
    value=os.environ["CM_TEST_PG_DSN"]
    database=conninfo_to_dict(value).get("dbname","").lower()
    if "test" not in database and "_ci" not in database:
        raise RuntimeError("Refusing to use non-disposable PostgreSQL")
    return value


class Host:
    def __init__(self,accept=True):
        self.accept=accept
        self.calls=0

    def verify_subtitle_persistence(self,analysis,manifest,proposal):
        self.calls+=1
        return self.accept is True and analysis>0 and proposal.verify_digest()


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL DSN")
class TestB6Pg17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=safe_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def setup_parent(self,*,no_subtitles=False):
        m,p=scene(with_ocr=not no_subtitles)
        if no_subtitles:
            s=scan(m,subtitle_type="none")
        else:
            s=scan(m)
        proposal=validate_b6_subtitles(
            manifest=m,packet=p,scan=s,detector_verifier=Detector()
        )
        self.assertTrue(proposal.can_persist)
        source="b6-source-"+uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",(source,)
            )
            key=uuid4().hex
            video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,'video') RETURNING id""",
                (key,"https://example.test/b6/"+key)
            ).fetchone()[0]
        m=replace(m,source_id=source,video_id=video,source_policy_version_id=None)
        m=replace(m,manifest_sha256=canonical_hash(m.payload()))
        # Re-create actual signed scan/packet/proposal under the altered test
        # database parent lineage; never just rewrite a proposal's digest.
        raw=copy.deepcopy(p.payload)
        raw["video_id"]=video
        raw["source_policy_version_id"]=None
        raw["source_evidence_manifest_sha256"]=m.manifest_sha256
        from intelligence.group_b.context.builder import ContextPacket
        p=ContextPacket(raw,canonical_hash(raw),1024,3000)
        s=scan(m,subtitle_type="none" if no_subtitles else "standard")
        proposal=validate_b6_subtitles(
            manifest=m,packet=p,scan=s,detector_verifier=Detector()
        )
        pins={
            "group_b_project_id":m.project_id,
            "group_b_run_id":m.b_run_id,
            "group_b_media_evidence_manifest_sha256":m.manifest_sha256,
            "group_b_b6_subtitle_proposal_sha256":proposal.proposal_sha256,
        }
        with self.psycopg.connect(self.dsn) as conn:
            analysis=conn.execute(
                """INSERT INTO content_b_analyses(
                     video_id,source_id,analysis_key,analysis_version,
                     content_type,method_name,method_version,state,method_details)
                   VALUES(%s,%s,%s,'v1','video','b6-fixture','1','pending',
                          %s::jsonb) RETURNING id""",
                (video,source,uuid4().hex,json.dumps(pins,sort_keys=True))
            ).fetchone()[0]
        return analysis,m,proposal

    def test_sql005_full_style_write_and_no_replay(self):
        a,m,p=self.setup_parent()
        with self.psycopg.connect(self.dsn) as conn:
            saved=persist_b6_subtitles(
                conn,analysis_id=a,manifest=m,proposal=p,
                admission_verifier=Host(),
            )
        self.assertEqual(saved["subtitle_type"],"standard")
        self.assertEqual(saved["highlight_color"],None)
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                """SELECT subtitle_type,position,font_size,font_style,text_color,
                          highlight_color,background_style,display_mode
                   FROM content_b_subtitles WHERE analysis_id=%s""",
                (a,)
            ).fetchone()
            self.assertEqual(row,tuple(saved[k] for k in saved))
            self.assertEqual(conn.execute(
                "SELECT state FROM content_b_analyses WHERE id=%s",(a,)
            ).fetchone()[0],"partial")
            with self.assertRaises(B6SubtitleError):
                persist_b6_subtitles(
                    conn,analysis_id=a,manifest=m,proposal=p,
                    admission_verifier=Host(),
                )

    def test_none_has_all_null_style_and_cannot_forge_unknown(self):
        a,m,p=self.setup_parent(no_subtitles=True)
        with self.psycopg.connect(self.dsn) as conn:
            result=persist_b6_subtitles(
                conn,analysis_id=a,manifest=m,proposal=p,
                admission_verifier=Host(),
            )
        self.assertEqual(result["subtitle_type"],"none")
        self.assertTrue(all(v is None for k,v in result.items()
                            if k!="subtitle_type"))
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B6SubtitleError):
                persist_b6_subtitles(
                    conn,analysis_id=a,manifest=m,
                    proposal=unknown_b6(manifest=m,reason="ocr_missing"),
                    admission_verifier=Host(),
                )

    def test_host_denial_and_changed_parent_pin_no_sql(self):
        a,m,p=self.setup_parent()
        for host in (None,Host(False)):
            with self.psycopg.connect(self.dsn) as conn:
                with self.assertRaises(B6SubtitleError):
                    persist_b6_subtitles(
                        conn,analysis_id=a,manifest=m,proposal=p,
                        admission_verifier=host,
                    )
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """UPDATE content_b_analyses SET method_details=jsonb_set(
                       method_details,'{group_b_b6_subtitle_proposal_sha256}',
                       to_jsonb('tampered'::text))
                   WHERE id=%s""",(a,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(B6SubtitleError):
                persist_b6_subtitles(
                    conn,analysis_id=a,manifest=m,proposal=p,
                    admission_verifier=Host(),
                )
            self.assertFalse(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM content_b_subtitles WHERE analysis_id=%s)",
                (a,),
            ).fetchone()[0])


if __name__=="__main__":
    unittest.main()
