"""M41 project-scoped S4 fixture leasing: SQL and DB mutation guards."""
import unittest
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch

from intelligence.relevance import offline_scoped_worker as w
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
OTHER="b"*32
RID="11111111-1111-4111-8111-111111111111"


class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class FakeConnection:
    def __init__(self,*,row=None,reaped=None):
        self.sql=[]
        self.row=row
        self.reaped=reaped
        self.claimed=False
    @contextmanager
    def transaction(self):
        yield self
    def execute(self,query,args=None):
        self.sql.append((query,args))
        if query==w.WORKSPACE:
            return Cursor((1,))
        if query==w.EXHAUSTED_SQL:
            r=self.reaped;self.reaped=None
            return Cursor(r)
        if query==w.CLAIM_SQL:
            if self.claimed:return Cursor(None)
            self.claimed=True
            return Cursor(self.row)
        if query==w.UPDATE_ITEM:
            return Cursor((self.row[2],))
        if query in (w.UPDATE_RUN,w.UPDATE_ALIAS):
            return Cursor(None)
        raise AssertionError("unexpected SQL: "+query[:120])


class OfflineScopedTests(unittest.TestCase):
    def test_claim_and_expired_reap_always_filter_project_before_row_lock(self):
        self.assertIn("WHERE r.project_id=%s",w.CLAIM_SQL)
        self.assertIn("WHERE r.project_id=%s",w.EXHAUSTED_SQL)
        self.assertIn("FOR UPDATE OF r,i SKIP LOCKED",w.CLAIM_SQL)
        self.assertIn("FOR UPDATE OF r,i SKIP LOCKED",w.EXHAUSTED_SQL)
        self.assertIn("AND project_id=%s",w.UPDATE_ITEM)
        self.assertNotIn("sre_live_runs",w.CLAIM_SQL)
        self.assertNotIn("dispatch",w.CLAIM_SQL.lower())
        self.assertNotIn("opus",w.CLAIM_SQL.lower())

    def test_work_once_does_not_scan_other_projects_or_write_live(self):
        db=FakeConnection()
        result=w.work_once_for_project(db,PID)
        self.assertEqual(result["status"],"drained")
        self.assertEqual(result["paid_post_calls"],0)
        self.assertEqual(result["live_results_written"],0)
        self.assertTrue(all(args[0]==PID for sql,args in db.sql if
                            sql in (w.EXHAUSTED_SQL,w.CLAIM_SQL,w.WORKSPACE)))

    def test_claim_uses_original_fenced_s4_settlement_only(self):
        row=(RID,PID,9,21,34,"f"*64,{}, "e"*64,4)
        db=FakeConnection(row=row)
        with patch.object(w,"finish_item",return_value=True) as finish:
            with patch.object(w,"fail_item") as fail:
                result=w.work_once_for_project(db,PID,lease_seconds=15)
        self.assertEqual(result["status"],"processed")
        self.assertEqual(result["processed"],1)
        finish.assert_called_once()
        fail.assert_not_called()
        claimed=finish.call_args.args[1]
        self.assertEqual(claimed["project_id"],PID)
        self.assertEqual(claimed["generation"],5)
        self.assertEqual(len(claimed["worker_token"]),36)
        self.assertEqual(result["paid_post_calls"],0)
        self.assertEqual(result["live_results_written"],0)
        self.assertEqual(db.sql[-3][0],w.UPDATE_ITEM)
        self.assertEqual(db.sql[-3][1][2],15)

    def test_wrong_tenant_row_cannot_claim_or_finalize(self):
        row=(RID,OTHER,9,21,34,"f"*64,{}, "e"*64,4)
        db=FakeConnection(row=row)
        with patch.object(w,"finish_item") as finish:
            with self.assertRaises(SREContractError) as exc:
                w.work_once_for_project(db,PID)
        self.assertEqual(exc.exception.code,"rights_denied")
        finish.assert_not_called()

    def test_fenced_failure_uses_original_s4_safe_retry_only(self):
        row=(RID,PID,9,21,34,"f"*64,{}, "e"*64,4)
        db=FakeConnection(row=row)
        with patch.object(w,"finish_item",side_effect=RuntimeError("fixture fail")):
            with patch.object(w,"fail_item",return_value=True) as fail:
                with self.assertRaises(RuntimeError):
                    w.work_once_for_project(db,PID)
                fail.assert_called_once()

    def test_reaper_only_exhausted_for_one_explicit_project(self):
        db=FakeConnection(reaped=(RID,PID,19))
        with patch.object(w,"_stale_item") as stale:
            num=w._reap(db,PID)
        self.assertEqual(num,1)
        stale.assert_called_once_with(db,RID,PID,19)
        for sql,args in db.sql:
            if sql==w.EXHAUSTED_SQL:
                self.assertEqual(args,(PID,))

    def test_invalid_scope_and_lease_never_queries(self):
        for tenant,lease in ((OTHER.upper(),60),(PID,0),(PID,3601)):
            db=FakeConnection()
            with self.assertRaises(SREContractError):
                w.work_once_for_project(db,tenant,lease_seconds=lease)
            self.assertEqual(db.sql,[])


if __name__=="__main__":
    unittest.main()
