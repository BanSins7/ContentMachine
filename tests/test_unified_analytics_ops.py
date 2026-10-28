"""UA-MOD-28: deterministic operational metrics + CLI tests.

Mocked DB represents synthetic internal aggregate results; it does NOT
qualify real SRE/QDP/A/B/C snapshots or execute PostgreSQL17 migrations.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import ops_observability as o
from intelligence.unified_analytics import ops_cli as cli

PROJECT="a"*32
OTHER="b"*32
SECRET="c"*64
NOW=datetime(2026,10,28,9,0,tzinfo=timezone.utc)


class Cursor:
    def __init__(self,rows):self.data=rows
    def fetchall(self):return self.data


class MockDB:
    def __init__(self):
        self.info=SimpleNamespace(transaction_status=0)
        self.sql=[]
        self.writes=[]
        self.project_exists=True
        self.bad_metric=False
        self.now=NOW
        self.queue=[
            dict(state="pending",n=3,oldest_waiting_at=NOW-timedelta(minutes=12),
                 expired_leases=0,retried_jobs=0,delayed_jobs=1),
            dict(state="retry",n=2,oldest_waiting_at=NOW-timedelta(seconds=45),
                 expired_leases=0,retried_jobs=2,delayed_jobs=1),
            dict(state="leased",n=2,oldest_waiting_at=None,
                 expired_leases=1,retried_jobs=1,delayed_jobs=0),
            dict(state="succeeded",n=1,oldest_waiting_at=None,
                 expired_leases=0,retried_jobs=0,delayed_jobs=0),
        ]
        self.snap=[
            dict(status="partial",n=4),dict(status="blocked",n=2)
        ]
        self.evidence=[
            dict(port="group_a",state="missing",checked=False,is_current=False,n=3),
            dict(port="group_b",state="partial",checked=False,is_current=False,n=2),
            dict(port="group_c",state="revoked",checked=False,is_current=False,n=1),
            dict(port="source_policy",state="unavailable",checked=False,is_current=False,n=2),
            dict(port="qdp",state="complete",checked=True,is_current=True,n=2),
        ]
        self.outbox=[dict(delivery_state="pending",n=2)]
        self.events=[
            dict(to_state="leased",n=3),dict(to_state="retry",n=2)
        ]
        self.invalidations=[dict(n=4)]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    @contextmanager
    def transaction(self):
        yield
    def execute(self,sql,args=()):
        self.sql.append((sql,args))
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
            return Cursor([])
        if sql==o.CLOCK_SQL:return Cursor([dict(db_now=self.now)])
        if sql==o.PROJECT_SQL:
            return Cursor([{"id":PROJECT}] if self.project_exists and args==(PROJECT,) else [])
        if args and args[0]!=PROJECT and sql!=o.QUEUE_SQL:
            raise AssertionError("unscoped query")
        if sql==o.QUEUE_SQL:
            assert args==(NOW,NOW,PROJECT)
            return Cursor(self.queue)
        if sql==o.SNAPSHOT_SQL:return Cursor(self.snap)
        if sql==o.EVIDENCE_SQL:return Cursor(self.evidence)
        if sql==o.INVALIDATIONS_SQL:return Cursor(self.invalidations)
        if sql==o.OUTBOX_SQL:return Cursor(self.outbox)
        if sql==o.EVENTS_SQL:return Cursor(self.events)
        raise AssertionError("unexpected SQL "+sql[:60])


def report(db=None):
    db=db or MockDB()
    return o.ProjectOpsReader(db).inspect(PROJECT)


class OpsReaderTests(unittest.TestCase):
    def test_core_metrics_exact_golden(self):
        r=report()
        self.assertEqual(r.queue_states["pending"],3)
        self.assertEqual(r.queue_states["retry"],2)
        self.assertEqual(r.queue_states["leased"],2)
        self.assertEqual(r.queue_lag_seconds,720)
        self.assertEqual(r.delayed_jobs,2)
        self.assertEqual(r.expired_leases,1)
        self.assertEqual(r.retried_jobs,3)
        self.assertEqual(r.snapshot_diagnostics,{"partial":4,"blocked":2})

    def test_missing_a_b_c_and_current_source(self):
        r=report()
        self.assertEqual(r.missing_ports["group_a"],3)
        self.assertEqual(r.partial_ports["group_b"],2)
        self.assertEqual(r.revoked_ports["group_c"],1)
        self.assertEqual(r.source_stale_evidence,2)
        self.assertEqual(r.missing_ports["source_policy"],2)

    def test_outbox_revocation_events_and_safe_job_transitions(self):
        r=report()
        self.assertEqual(r.outbox_states["pending"],2)
        self.assertEqual(r.outbox_states["failed"],0)
        self.assertEqual(r.invalidated_snapshots,4)
        self.assertEqual(r.read_revocation_events,4)
        self.assertEqual(r.queue_transition_counts["leased"],3)
        self.assertEqual(r.queue_transition_counts["retry"],2)

    def test_no_qualified_claim_even_if_historical(self):
        r=report()
        self.assertFalse(r.qualified_live_readable)
        self.assertNotIn("qualified",r.snapshot_diagnostics)
        self.assertNotIn("breakout_score",r.safe_json())

    def test_report_contains_no_project_or_job_id(self):
        r=report()
        obj=json.loads(r.safe_json())
        self.assertEqual(obj["schema_version"],o.OPS_WIRE)
        self.assertNotIn(PROJECT,r.safe_json())
        self.assertNotIn("job_id",r.safe_json())
        self.assertNotIn("dsn",r.safe_json())
        self.assertNotIn("token",r.safe_json())

    def test_one_fresh_repeatable_read_and_scoped_queries(self):
        db=MockDB();report(db)
        self.assertEqual(db.sql[0][0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        self.assertEqual(len([x for x,_ in db.sql if x==o.CLOCK_SQL]),1)
        for sql,args in db.sql:
            if sql==o.QUEUE_SQL:self.assertEqual(args[-1],PROJECT)
            elif sql not in (
                o.CLOCK_SQL,"SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            ):self.assertEqual(args,(PROJECT,))
        self.assertEqual(db.writes,[])

    def test_empty_project_zero_counts_no_lag(self):
        db=MockDB()
        db.queue=[];db.snap=[];db.evidence=[]
        db.invalidations=[{"n":0}];db.outbox=[];db.events=[]
        r=report(db)
        self.assertIsNone(r.queue_lag_seconds)
        self.assertEqual(sum(r.queue_states.values()),0)
        self.assertEqual(sum(r.missing_ports.values()),0)
        self.assertFalse(r.qualified_live_readable)

    def test_project_unknown_denied_before_metrics(self):
        db=MockDB();db.project_exists=False
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)
        self.assertNotIn(o.QUEUE_SQL,[sql for sql,_ in db.sql])

    def test_wrong_project_id_denied_before_DB(self):
        db=MockDB()
        with self.assertRaises(AnalyticsBoundaryError):
            o.ProjectOpsReader(db).inspect("not-project")
        self.assertEqual(db.sql,[])

    def test_active_transaction_refused(self):
        db=MockDB();db.info.transaction_status=2
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)
        self.assertEqual(db.sql,[])

    def test_bad_queue_state_deny_no_silent_ignore(self):
        db=MockDB();db.queue[0]["state"]="qualified"
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_duplicate_queue_state_refused_even_count_zero(self):
        db=MockDB();db.queue=[dict(db.queue[0]),dict(db.queue[0])]
        db.queue[0]["n"]=0
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_bad_evidence_port_refused(self):
        db=MockDB();db.evidence[0]["port"]="private_account_id"
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_evidence_row_duplicate_exact_group_denied(self):
        db=MockDB();db.evidence.append(dict(db.evidence[0]))
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_raw_error_reason_not_selected_or_logged(self):
        self.assertNotIn("reason_code",o.EVIDENCE_SQL)
        self.assertNotIn("payload",o.EVIDENCE_SQL)
        self.assertNotIn("creator",o.EVIDENCE_SQL)
        self.assertNotIn("calculated_metrics",o.EVIDENCE_SQL)

    def test_stale_future_timestamp_denied(self):
        db=MockDB()
        db.queue[0]["oldest_waiting_at"]=NOW+timedelta(days=2)
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_invalid_db_number_deny(self):
        db=MockDB();db.queue[0]["n"]=-1
        with self.assertRaises(AnalyticsBoundaryError):
            report(db)

    def test_no_unbounded_telemetry_categories(self):
        self.assertLessEqual(len(o.EVENT_CODES),20)
        self.assertLessEqual(len(o.PORTS),7)
        self.assertLessEqual(o.MAX_SAMPLE_ROWS,140)


class InMemoryCountersTests(unittest.TestCase):
    def test_fixed_event_and_duration_buckets(self):
        c=o.SafeEventCounters()
        c.record("queue_retry",duration_seconds=.005)
        c.record("group_b_missing",duration_seconds=4)
        self.assertEqual(c.snapshot()["events"]["queue_retry"],1)
        self.assertEqual(c.snapshot()["events"]["group_b_missing"],1)
        self.assertEqual(c.snapshot()["latency_buckets"]["lt_10ms"],1)
        self.assertEqual(c.snapshot()["latency_buckets"]["lt_10s"],1)

    def test_raw_id_or_secret_event_not_accepted(self):
        c=o.SafeEventCounters()
        for value in ("token:secret","project"+PROJECT,"unknown_exception",None):
            c.record(value)
        self.assertEqual(sum(c.snapshot()["events"].values()),0)

    def test_nan_inf_and_negative_duration_rejected(self):
        c=o.SafeEventCounters()
        for value in (-1,float("nan"),float("inf"),"secret"):
            c.record("queue_retry",duration_seconds=value)
        self.assertEqual(c.snapshot()["events"]["queue_retry"],0)

    def test_failure_classification_no_raw_exception_string(self):
        self.assertEqual(o.classify_failure("worker_lease_lost"),
                         "queue_lease_lost")
        self.assertEqual(o.classify_failure("worker_storage_unavailable"),
                         "pg_retry")
        self.assertEqual(o.classify_failure("group_c_missing"),
                         "group_c_missing")
        self.assertEqual(o.classify_failure("db_password=secret"),
                         "operation_denied")

    def test_trace_uuid_entropy_no_project_data(self):
        a=o.new_trace_id();b=o.new_trace_id()
        self.assertEqual(len(a),32)
        self.assertNotEqual(a,b)
        self.assertNotEqual(a,PROJECT)


def env():
    return {
        cli.OPS_MAP_ENV:json.dumps({
            PROJECT:sha256(bytes.fromhex(SECRET)).hexdigest()
        }),
        cli.OPS_TOKEN_ENV:SECRET,
        cli.OPS_DSN_ENV:"postgresql://test-only-unavailable",
    }


class QueueRecovery:
    def __init__(self,db):self.db=db
    def recover_expired(self,project_id,*,limit):
        self.db.writes.append((project_id,limit))
        first=self.db.recovery
        self.db.recovery=()
        return first


class CLITests(unittest.TestCase):
    def setUp(self):
        self.db=MockDB()
        self.db.recovery=(("job-redacted-1","retry"),
                          ("job-redacted-2","dead"))
        self.out=StringIO()
        self.env=env()

    def run_cli(self,argv):
        return cli.main(argv,connect=lambda:self.db,
                        environ=self.env,out=self.out)

    def test_authorize_explicit_project_only(self):
        self.assertTrue(cli.authorize(PROJECT,self.env))
        self.assertFalse(cli.authorize(OTHER,self.env))
        self.assertFalse(cli.authorize("malformed",self.env))

    def test_secret_mismatch_denied_before_connect(self):
        self.env[cli.OPS_TOKEN_ENV]="d"*64
        called=[]
        def connect():called.append(1);return self.db
        with self.assertRaises(cli.OpsCLIError):
            cli.main(["--project",PROJECT,"--inspect"],
                     connect=connect,environ=self.env,out=self.out)
        self.assertEqual(called,[])

    def test_no_map_denied_even_if_token_present(self):
        self.env.pop(cli.OPS_MAP_ENV)
        with self.assertRaises(cli.OpsCLIError):
            self.run_cli(["--project",PROJECT,"--inspect"])
        self.assertEqual(self.db.sql,[])

    def test_readonly_cli_golden_one_json_line(self):
        result=self.run_cli(["--project",PROJECT,"--inspect"])
        self.assertEqual(result,0)
        data=json.loads(self.out.getvalue())
        self.assertEqual(data["cli_schema_version"],cli.OPS_CLI_WIRE)
        self.assertEqual(data["report"]["queue_states"]["pending"],3)
        self.assertEqual(len(data["trace_id"]),32)
        self.assertEqual(self.db.writes,[])

    def test_stdout_has_no_project_token_dsn_jobids(self):
        self.run_cli(["--project",PROJECT,"--inspect"])
        txt=self.out.getvalue()
        for secret in (PROJECT,SECRET,self.env[cli.OPS_DSN_ENV],
                       "job-redacted-1","payload","media_url"):
            self.assertNotIn(secret,txt)

    def test_recover_without_apply_denied_preconnect(self):
        with self.assertRaises(cli.OpsCLIError):
            self.run_cli(["--project",PROJECT,"--recover-expired",
                          "--confirm-project",PROJECT])
        self.assertEqual(self.db.sql,[])

    def test_recover_without_matching_confirm_denied(self):
        with self.assertRaises(cli.OpsCLIError):
            self.run_cli(["--project",PROJECT,"--recover-expired",
                          "--apply","--confirm-project",OTHER])
        self.assertEqual(self.db.writes,[])

    def test_inspect_apply_disallowed(self):
        with self.assertRaises(cli.OpsCLIError):
            self.run_cli(["--project",PROJECT,"--inspect","--apply"])
        self.assertEqual(self.db.sql,[])

    def test_recover_exact_confirmation_and_no_job_ids(self):
        with patch.object(cli,"QualificationJobQueue",QueueRecovery):
            result=self.run_cli([
                "--project",PROJECT,"--recover-expired","--apply",
                "--confirm-project",PROJECT,"--limit","5",
            ])
        self.assertEqual(result,0)
        data=json.loads(self.out.getvalue())["report"]
        self.assertEqual(data["recovered_jobs"],2)
        self.assertEqual(data["states"],{"retry":1,"dead":1})
        self.assertFalse(data["qualified"])
        self.assertFalse(data["producer_run_started"])
        self.assertEqual(self.db.writes,[(PROJECT,5)])
        self.assertNotIn("job-redacted",self.out.getvalue())

    def test_recover_repeat_same_db_is_idempotently_empty(self):
        with patch.object(cli,"QualificationJobQueue",QueueRecovery):
            cmd=["--project",PROJECT,"--recover-expired","--apply",
                 "--confirm-project",PROJECT]
            self.run_cli(cmd)
            self.out=StringIO()
            self.run_cli(cmd)
        self.assertEqual(json.loads(self.out.getvalue())["report"]["recovered_jobs"],0)
        self.assertEqual(len(self.db.writes),2)

    def test_recover_invalid_limit_denied_preconnect(self):
        for limit in ("0","101","-1"):
            with self.assertRaises(cli.OpsCLIError):
                self.run_cli(["--project",PROJECT,"--recover-expired","--apply",
                              "--confirm-project",PROJECT,"--limit",limit])
        self.assertEqual(self.db.writes,[])

    def test_unknown_recovery_state_not_printed(self):
        self.db.recovery=(("job1","qualified"),)
        with patch.object(cli,"QualificationJobQueue",QueueRecovery):
            with self.assertRaises(cli.OpsCLIError):
                self.run_cli(["--project",PROJECT,"--recover-expired",
                              "--apply","--confirm-project",PROJECT])
        self.assertEqual(self.out.getvalue(),"")

    def test_nonmatching_project_token_preconnect(self):
        before=list(self.db.sql)
        with self.assertRaises(cli.OpsCLIError):
            self.run_cli(["--project",OTHER,"--inspect"])
        self.assertEqual(self.db.sql,before)


if __name__=="__main__":
    unittest.main()
