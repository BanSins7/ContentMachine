"""UA-MOD-29 synthetic source lifecycle and operator CLI regressions.

No production DB nor actual approved Group C. Source policy and peer
modules are never mutated by UAE-only retention operations.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from hashlib import sha256
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import source_lifecycle as lc
from intelligence.unified_analytics import retention_cli as cli
from intelligence.unified_analytics.ops_cli import (
    OPS_TOKEN_ENV,OPS_MAP_ENV,OPS_DSN_ENV
)

PROJECT="a"*32
OTHER="b"*32
SOURCE="uae.test.source"
SECRET="c"*64
A=str(uuid4())


class Cursor:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return self.rows


class DB:
    def __init__(self):
        self.info=SimpleNamespace(transaction_status=0)
        self.queries=[]
        self.project_exists=True
        self.source_exists=True
        self.counts={
            "requests":2,"snapshots":3,"revisions":1,
            "project_qdp":5,"project_qdp_quarantine":2,
            "project_audit":2,"project_runs":1,
        }
        self.targets=[
            {"snapshot_id":10,"reason_code":"source_access_revoked"},
            {"snapshot_id":11,"reason_code":"source_retention_expired"}
        ]
        self.invalidations=set()
        self.outbox=[]
        self.fail_outbox=False
        self.no_purge=False
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):
        old=(copy.deepcopy(self.counts),copy.deepcopy(self.invalidations),
             copy.deepcopy(self.outbox))
        try:yield
        except BaseException:
            self.counts,self.invalidations,self.outbox=old
            raise
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        if sql.startswith("SET TRANSACTION ISOLATION LEVEL"):
            return Cursor([])
        if sql==lc.PROJECT_LOCK_SQL:
            return Cursor([{"id":PROJECT}] if self.project_exists and args==(PROJECT,) else [])
        if sql==lc.SOURCE_SQL:
            return Cursor([{"id":SOURCE}] if self.source_exists and args==(SOURCE,) else [])
        if sql==lc.COUNTS_SQL:
            assert args==(PROJECT,SOURCE,PROJECT,SOURCE,PROJECT,SOURCE,
                          PROJECT,PROJECT,PROJECT,PROJECT)
            return Cursor([dict(self.counts)])
        if sql==lc.FINGERPRINT_SQL:
            assert args==(PROJECT,SOURCE,PROJECT,SOURCE,PROJECT,SOURCE,
                          PROJECT,PROJECT,PROJECT,PROJECT)
            return Cursor([{
                k:sha256((k+":"+str(v)+":"+str(getattr(
                    self,"fingerprint_generation",0))).encode()).hexdigest()
                for k,v in self.counts.items()
            }])
        if sql==lc.REVOCATION_TARGETS_SQL:
            assert args[0:2]==(PROJECT,SOURCE)
            return Cursor([x for x in self.targets
                           if x["snapshot_id"] not in self.invalidations][:args[2]])
        if sql==lc.INSERT_INVALIDATION_SQL:
            assert args[0]==PROJECT
            sid=args[1]
            if sid in self.invalidations:return Cursor([])
            self.invalidations.add(sid)
            return Cursor([{"id":str(uuid4())}])
        if sql==lc.OUTBOX_INSERT_SQL:
            if self.fail_outbox:
                raise RuntimeError("postgresql://private-token@host")
            self.outbox.append(args)
            return Cursor([])
        deletions={
            lc.PURGE_PROJECT_RUNS_SQL:"project_runs",
            lc.PURGE_PROJECT_AUDIT_SQL:"project_audit",
            lc.PURGE_PROJECT_QDP_QUARANTINE_SQL:"project_qdp_quarantine",
            lc.PURGE_PROJECT_QDP_SQL:"project_qdp",
            lc.PURGE_REVISIONS_SQL:"revisions",
            lc.PURGE_REQUESTS_SQL:"requests",
        }
        if sql in deletions:
            key=deletions[sql]
            if not self.no_purge:self.counts[key]=0
            if sql==lc.PURGE_REQUESTS_SQL and not self.no_purge:
                self.counts["snapshots"]=0
                self.invalidations.clear();self.outbox.clear()
            return Cursor([])
        raise AssertionError("Unexpected lifecycle SQL")


def make_env():
    return {
        OPS_TOKEN_ENV:SECRET,
        OPS_MAP_ENV:json.dumps({
            PROJECT:sha256(bytes.fromhex(SECRET)).hexdigest()
        }),
        OPS_DSN_ENV:"postgresql://local-test-not-used",
    }


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.db=DB()
        self.lifecycle=lc.SourceLifecycle(self.db)
    def test_preview_golden_counts_conservative_collateral(self):
        p=self.lifecycle.preview(PROJECT,SOURCE)
        self.assertEqual(p.counts["requests"],2)
        self.assertEqual(p.counts["snapshots"],3)
        self.assertEqual(p.counts["revisions"],1)
        self.assertEqual(p.counts["project_qdp"],5)
        self.assertTrue(p.project_wide_collateral)
        self.assertEqual(len(p.scope_sha256),64)
    def test_preview_is_repeatable_and_read_only(self):
        a=self.lifecycle.preview(PROJECT,SOURCE)
        b=self.lifecycle.preview(PROJECT,SOURCE)
        self.assertEqual(a,b)
        self.assertEqual(self.db.invalidations,set())
        self.assertEqual(self.db.outbox,[])
    def test_preview_does_not_delete_other_source(self):
        self.lifecycle.preview(PROJECT,SOURCE)
        self.assertFalse(any("DELETE FROM" in sql for sql,_ in self.db.queries))
    def test_project_mismatch_deny_before_db(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.preview("not_hex",SOURCE)
        self.assertEqual(self.db.queries,[])
    def test_invalid_source_scope_denied_before_db(self):
        for source in ("",SOURCE+";DROP TABLE",None,False):
            with self.assertRaises(AnalyticsBoundaryError):
                self.lifecycle.preview(PROJECT,source)
        self.assertEqual(self.db.queries,[])
    def test_missing_project_denied_before_plan(self):
        self.db.project_exists=False
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.preview(PROJECT,SOURCE)
        self.assertNotIn(lc.COUNTS_SQL,[x for x,_ in self.db.queries])
    def test_missing_source_denied_before_plan(self):
        self.db.source_exists=False
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.preview(PROJECT,SOURCE)
        self.assertNotIn(lc.COUNTS_SQL,[x for x,_ in self.db.queries])
    def test_existing_db_transaction_denied(self):
        self.db.info.transaction_status=2
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.preview(PROJECT,SOURCE)
        self.assertEqual(self.db.queries,[])
    def test_revocation_two_current_invalidations_and_outbox(self):
        out=self.lifecycle.invalidate_unreadable(PROJECT,SOURCE,limit=20)
        self.assertEqual(out.operation,"invalidate")
        self.assertEqual(out.affected,2)
        self.assertFalse(out.qualified)
        self.assertEqual(self.db.invalidations,{10,11})
        self.assertEqual(len(self.db.outbox),2)
        self.assertEqual({x[1] for x in self.db.outbox},{10,11})
    def test_revocation_idempotent_noop(self):
        first=self.lifecycle.invalidate_unreadable(PROJECT,SOURCE)
        second=self.lifecycle.invalidate_unreadable(PROJECT,SOURCE)
        self.assertEqual((first.affected,second.affected),(2,0))
        self.assertTrue(second.idempotent_noop)
        self.assertEqual(len(self.db.outbox),2)
    def test_revocation_bounded_limit(self):
        out=self.lifecycle.invalidate_unreadable(PROJECT,SOURCE,limit=1)
        self.assertEqual(out.affected,1)
        self.assertEqual(len(self.db.outbox),1)
    def test_revocation_invalid_limits_fail_no_io(self):
        for bad in (0,101,True,"10"):
            with self.assertRaises(AnalyticsBoundaryError):
                self.lifecycle.invalidate_unreadable(PROJECT,SOURCE,limit=bad)
        self.assertEqual(self.db.queries,[])
    def test_revocation_outbox_exception_rolls_back(self):
        self.db.fail_outbox=True
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.invalidate_unreadable(PROJECT,SOURCE)
        self.assertEqual(self.db.invalidations,set())
        self.assertEqual(self.db.outbox,[])
    def test_revocation_unexpected_reason_denied(self):
        self.db.targets[0]["reason_code"]="qualified"
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.invalidate_unreadable(PROJECT,SOURCE)
        self.assertEqual(self.db.invalidations,set())
    def test_purge_exact_plan_confirmation_succeeds(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        out=self.lifecycle.purge(
            PROJECT,SOURCE,expected_plan_sha256=plan.scope_sha256,
            confirm_project=PROJECT,confirm_source=SOURCE,
            accept_project_wide_collateral=True
        )
        self.assertEqual(out.operation,"purge")
        self.assertEqual(out.affected,6)
        self.assertFalse(out.qualified)
        self.assertEqual(set(self.db.counts.values()),{0})
    def test_purge_conservatively_removes_project_qdp_audit_runs(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        self.lifecycle.purge(PROJECT,SOURCE,
            expected_plan_sha256=plan.scope_sha256,
            confirm_project=PROJECT,confirm_source=SOURCE)
        used=[sql for sql,_ in self.db.queries]
        for sql in (lc.PURGE_PROJECT_QDP_SQL,
                    lc.PURGE_PROJECT_QDP_QUARANTINE_SQL,
                    lc.PURGE_PROJECT_AUDIT_SQL,lc.PURGE_PROJECT_RUNS_SQL):
            self.assertIn(sql,used)
        self.assertIn(lc.PURGE_REQUESTS_SQL,used)
    def test_purge_project_wide_collateral_requires_separate_opt_in(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.lifecycle.purge(PROJECT,SOURCE,
                expected_plan_sha256=plan.scope_sha256,
                confirm_project=PROJECT,confirm_source=SOURCE)
        self.assertEqual(e.exception.code,
                         "lifecycle_project_collateral_confirmation_required")
        self.assertFalse(any("DELETE FROM" in sql for sql,_ in self.db.queries))

    def test_purge_without_source_confirmation_rejected_no_delete(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.purge(PROJECT,SOURCE,
                expected_plan_sha256=plan.scope_sha256,
                confirm_project=PROJECT,confirm_source="other")
        self.assertFalse(any("DELETE FROM" in sql for sql,_ in self.db.queries))
    def test_purge_without_digest_rejected(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.lifecycle.purge(PROJECT,SOURCE,expected_plan_sha256="",
                confirm_project=PROJECT,confirm_source=SOURCE,
                accept_project_wide_collateral=True)
        self.assertEqual(self.db.queries,[])
    def test_purge_stale_plan_rejected_no_deletes(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        self.db.counts["snapshots"]+=1
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.lifecycle.purge(PROJECT,SOURCE,
                expected_plan_sha256=plan.scope_sha256,
                confirm_project=PROJECT,confirm_source=SOURCE,
                accept_project_wide_collateral=True)
        self.assertEqual(e.exception.code,"lifecycle_plan_stale")
        self.assertFalse(any("DELETE FROM" in sql for sql,_ in self.db.queries))
    def test_purge_same_count_different_identity_rejected(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        self.db.fingerprint_generation=1
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.lifecycle.purge(PROJECT,SOURCE,
                expected_plan_sha256=plan.scope_sha256,
                confirm_project=PROJECT,confirm_source=SOURCE,
                accept_project_wide_collateral=True)
        self.assertEqual(e.exception.code,"lifecycle_plan_stale")
        self.assertEqual(self.db.counts["requests"],2)
        self.assertFalse(any("DELETE FROM" in sql for sql,_ in self.db.queries))

    def test_purge_failed_cascade_rolls_back_transaction(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        self.db.no_purge=True
        with self.assertRaises(AnalyticsBoundaryError) as e:
            self.lifecycle.purge(PROJECT,SOURCE,
                expected_plan_sha256=plan.scope_sha256,
                confirm_project=PROJECT,confirm_source=SOURCE,
                accept_project_wide_collateral=True)
        self.assertEqual(e.exception.code,"lifecycle_purge_incomplete")
        self.assertEqual(self.db.counts["requests"],2)
    def test_purge_retry_with_fresh_noop_plan(self):
        first=self.lifecycle.preview(PROJECT,SOURCE)
        self.lifecycle.purge(PROJECT,SOURCE,
            expected_plan_sha256=first.scope_sha256,
            confirm_project=PROJECT,confirm_source=SOURCE)
        second=self.lifecycle.preview(PROJECT,SOURCE)
        out=self.lifecycle.purge(PROJECT,SOURCE,
            expected_plan_sha256=second.scope_sha256,
            confirm_project=PROJECT,confirm_source=SOURCE)
        self.assertTrue(out.idempotent_noop)
        self.assertEqual(out.affected,0)
    def test_no_source_rows_no_unrelated_project_wide_purge(self):
        self.db.counts.update(requests=0,snapshots=0,revisions=0)
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        self.assertFalse(plan.project_wide_collateral)
        out=self.lifecycle.purge(PROJECT,SOURCE,
            expected_plan_sha256=plan.scope_sha256,
            confirm_project=PROJECT,confirm_source=SOURCE)
        self.assertTrue(out.idempotent_noop)
        self.assertEqual(self.db.counts["project_qdp"],5)
    def test_source_project_digest_ensures_distinct_identities(self):
        plan=self.lifecycle.preview(PROJECT,SOURCE)
        identities={
            k:sha256((k+":"+str(v)+":0").encode()).hexdigest()
            for k,v in plan.counts.items()
        }
        self.assertEqual(plan.scope_sha256,lc.canonical_hash({
            "wire":lc.LIFECYCLE_WIRE,
            "project":PROJECT,"source":SOURCE,
            "counts":plan.counts,"source_present":True,
            "identity_sha256s":identities,
        }))
    def test_no_source_peer_deletion_sql(self):
        for sql in (lc.PURGE_REQUESTS_SQL,lc.PURGE_REVISIONS_SQL,
                    lc.PURGE_PROJECT_QDP_SQL,lc.PURGE_PROJECT_AUDIT_SQL):
            self.assertNotIn("group_a_",sql)
            self.assertNotIn("group_b_",sql)
            self.assertNotIn("group_c_",sql)
            self.assertNotIn("DELETE FROM sources",sql)
    def test_stale_rights_not_qualified(self):
        out=self.lifecycle.invalidate_unreadable(PROJECT,SOURCE)
        self.assertFalse(out.qualified)


class RetentionCLITests(unittest.TestCase):
    def setUp(self):
        self.db=DB()
        self.env=make_env()
        self.out=StringIO()
        self.base=["--project",PROJECT,"--source",SOURCE]
    def call(self,extra):
        return cli.main(self.base+extra,connect=lambda:self.db,
                        environ=self.env,out=self.out)
    def test_operator_preview_readonly(self):
        self.assertEqual(self.call(["--preview"]),0)
        p=json.loads(self.out.getvalue())
        self.assertEqual(p["result"]["mode"],"preview")
        self.assertFalse(p["result"]["writes_performed"])
        self.assertEqual(len(p["trace_id"]),32)
        self.assertEqual(self.db.counts["requests"],2)
    def test_cli_no_operator_token_rejects_preconnect(self):
        self.env.pop(OPS_TOKEN_ENV)
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--preview"])
        self.assertEqual(self.db.queries,[])
    def test_cli_foreign_project_rejects_preconnect(self):
        self.base=["--project",OTHER,"--source",SOURCE]
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--preview"])
        self.assertEqual(self.db.queries,[])
    def test_cli_no_secrets_in_preview_output(self):
        self.call(["--preview"])
        data=self.out.getvalue()
        for secret in (PROJECT,SOURCE,SECRET,self.env[OPS_DSN_ENV],
                       "snapshot_id","lease_token"):
            self.assertNotIn(secret,data)
    def test_cli_invalidate_requires_apply_and_both_confirmations(self):
        for opts in (
            ["--invalidate"],
            ["--invalidate","--apply"],
            ["--invalidate","--apply","--confirm-project",PROJECT],
            ["--invalidate","--apply","--confirm-project",OTHER,
             "--confirm-source",SOURCE],
        ):
            with self.assertRaises(cli.RetentionCLIError):
                self.call(opts)
        self.assertEqual(self.db.queries,[])
    def test_cli_explicit_invalidation(self):
        result=self.call(["--invalidate","--apply",
                         "--confirm-project",PROJECT,
                         "--confirm-source",SOURCE,"--limit","1"])
        self.assertEqual(result,0)
        p=json.loads(self.out.getvalue())["result"]
        self.assertEqual(p["affected"],1)
        self.assertFalse(p["qualified"])
    def test_cli_purge_requires_stored_plan_hash(self):
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--purge","--apply",
                      "--confirm-project",PROJECT,
                      "--confirm-source",SOURCE])
        self.assertEqual(self.db.queries,[])
    def test_cli_purge_explicit_plan_hash_and_scope(self):
        plan=lc.SourceLifecycle(self.db).preview(PROJECT,SOURCE)
        self.assertEqual(self.call(["--purge","--apply",
                           "--confirm-project",PROJECT,
                           "--confirm-source",SOURCE,
                           "--plan-sha256",plan.scope_sha256,
                           "--accept-project-wide-collateral"]),0)
        self.assertEqual(json.loads(self.out.getvalue())["result"]["mode"],"purge")
        self.assertFalse(json.loads(self.out.getvalue())["result"]["external_source_data_deleted"])
    def test_cli_broad_cleanup_without_optin_refused(self):
        plan=lc.SourceLifecycle(self.db).preview(PROJECT,SOURCE)
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--purge","--apply",
                      "--confirm-project",PROJECT,
                      "--confirm-source",SOURCE,
                      "--plan-sha256",plan.scope_sha256])
        self.assertEqual(self.db.counts["requests"],2)

    def test_cli_wrong_plan_denied(self):
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--purge","--apply",
                      "--confirm-project",PROJECT,
                      "--confirm-source",SOURCE,
                      "--plan-sha256","0"*64])
        self.assertEqual(self.db.counts["requests"],2)
    def test_cli_preview_rejects_apply(self):
        with self.assertRaises(cli.RetentionCLIError):
            self.call(["--preview","--apply"])
        self.assertEqual(self.db.queries,[])
    def test_cli_no_automatic_retries_or_peer_calls(self):
        self.call(["--preview"])
        self.assertNotIn("qualified",self.out.getvalue())
        self.assertNotIn("worker_id",self.out.getvalue())


if __name__=="__main__":
    unittest.main()
