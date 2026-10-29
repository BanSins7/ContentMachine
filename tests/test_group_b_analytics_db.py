"""GB-MOD-25 optional disposable PG17 negative qualification tests.

Existing Group B fixture is deliberately PARTIAL and has no signed A,
provider receipt or live UAE consumer. All paths MUST therefore remain
blocked: no made-up successful Analytics handoff, C scores or UAE writes.
"""
from __future__ import annotations

import os
import unittest
from uuid import uuid4

from intelligence.migrate import apply_migrations
from intelligence.group_b.integration.analytics import (
    read_uae_handoff,_serializable,
)
from intelligence.group_b.integration.analytics_contract import (
    HandoffError,HandoffState,make_compatibility,
)
from intelligence.group_b.integration import (
    make_current_lineage,read_pinned_lineage,
)
import test_group_b_atomic_db as fixture


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("Group B Analytics QA refuses non-test databases")
    return dsn


class SyntheticUAEHost:
    def __init__(self,*,allow=True):
        self.allow=allow
        self.calls=[]
    def verify_current_lineage(self,project_id,run_id,pins,current,conn):
        self.calls.append("current")
        return (self.allow and current.verify_digest() and
                current.project_id==project_id and pins.run_id==run_id)
    def verify_uae_consumer_capability(self,capability,conn):
        self.calls.append("capability")
        return self.allow and capability.verify()
    def verify_uae_quality_gate(self,project_id,run_id,current,row,conn):
        self.calls.append("quality")
        return self.allow
    def verify_uae_exact_source(self,project_id,run_id,current,row,parts,extra,conn):
        self.calls.append("source")
        return self.allow


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL17")
class TestB25AnalyticsPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self):
        base=fixture.TestAtomicGroupBPG17("runTest")
        base.psycopg=self.psycopg
        base.dsn=self.dsn
        args=base.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            result=base.write(conn,args)
        return args[0],result

    def current(self,conn,manifest):
        pins,_=read_pinned_lineage(
            conn,project_id=manifest.project_id,run_id=manifest.b_run_id,
        )
        return make_current_lineage(
            project_id=pins.project_id,video_id=pins.video_id,
            source_id=pins.source_id,content_type=pins.content_type,
            qdp_run_id=pins.qdp_run_id,
            qdp_input_sha256=pins.qdp_input_sha256,
            ga_result_id=pins.ga_result_id or 999,
            ga_result_sha256=pins.ga_result_sha256,
            media_manifest_sha256=pins.media_manifest_sha256,
            source_policy_version_id=pins.source_policy_version_id,
            source_rights_active=True,
            source_media_bytes_current=True,
            qdp_accepted_and_current=True,
            ga_completed_and_current=True,
        )

    @staticmethod
    def compatible():
        return make_compatibility(
            understands_b25_version=True,
            understands_stale_invalidation=True,
            understands_immutable_review=True,
            understands_qdp_receipt=True,
            understands_carousel_na=True,
        )

    def test_partial_never_becomes_uae_qualified_with_permissive_test_host(self):
        m,res=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            current=self.current(conn,m)
            result=read_uae_handoff(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=current,compatibility=self.compatible(),
                verifier=SyntheticUAEHost(),
            )
        self.assertIs(result.state,HandoffState.BLOCKED)
        self.assertFalse(result.can_offer)
        self.assertFalse(result.uae_qualified)
        self.assertIsNone(result.payload)
        self.assertTrue(result.reasons)

    def test_missing_actual_producer_attestation_does_not_expose_model_text(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            current=self.current(conn,m)
            result=read_uae_handoff(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=current,compatibility=self.compatible(),
                verifier=SyntheticUAEHost(allow=False),
            )
            self.assertIs(result.state,HandoffState.BLOCKED)
            self.assertIsNone(result.payload)

    def test_cross_project_and_nonexistent_run_are_denied(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            c=self.current(conn,m)
            for p,run in (("different-project",m.b_run_id),
                          (m.project_id,str(uuid4()))):
                with self.subTest(project=p,run=run):
                    with self.assertRaises(Exception):
                        read_uae_handoff(
                            conn,project_id=p,run_id=run,
                            current=c,compatibility=self.compatible(),
                            verifier=SyntheticUAEHost(),
                        )

    def test_no_analytics_tables_created_as_side_effect(self):
        m,_=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            current=self.current(conn,m)
            before=conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s",
                (m.project_id,)
            ).fetchone()[0]
            read_uae_handoff(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                current=current,compatibility=self.compatible(),
                verifier=SyntheticUAEHost(),
            )
            after=conn.execute(
                "SELECT count(*) FROM group_b_outbox WHERE project_id=%s",
                (m.project_id,)
            ).fetchone()[0]
            self.assertEqual(before,after)


if __name__=="__main__":
    unittest.main()
