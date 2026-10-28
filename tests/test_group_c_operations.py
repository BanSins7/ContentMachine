"""GC28 monitoring/recovery: pure alert oracles and optional disposable PG17.

Recovery tests use test-only IAM that is NOT a production security provider.
No actual production recovery or telemetry transport is asserted.
"""
from __future__ import annotations

import io
import os
import unittest
from dataclasses import FrozenInstanceError
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.durable_worker import WorkerPolicy
from intelligence.group_c.operations import (
    OPS_VERSION,MonitoringPolicy,ProjectPulse,OperationalAlert,RecoveryReceipt,
    PostgreSQLGroupCOperations,alerts_for,_event,
)
from intelligence.group_c.http_api import GroupCHTTPAPI
from test_group_c_http_api import _env,_call,_TestIAM,HOST,ORIGIN,OTHER


def _metrics():
    return {
        "queued_jobs":0,"running_jobs":0,"expired_job_leases":0,
        "failed_jobs":0,"blocked_runs":0,"received_inbox":0,
        "inbox_conflicts":0,"pending_outbox":0,"expired_outbox_leases":0,
        "failed_outbox":0,"missing_metric_videos":0,
        "stale_metric_videos":0,"oldest_queue_seconds":None,
        "oldest_inbox_seconds":None,"oldest_outbox_seconds":None,
        "max_metric_age_seconds":None,
    }


class _TestRecoveryIAM:
    """Verifies exact signed test assertion / project/action/request digest."""
    def __init__(self,*,allowed=True):
        self.allowed=allowed
        self.calls=0
    def verify_recovery(
        self,conn,*,project_id,operation_id,action,
        signed_assertion,request_sha256,
    ):
        self.calls+=1
        if not self.allowed or signed_assertion!="synthetic-admin-recovery-assertion-token":
            return None
        return {
            "verified":True,"project_id":project_id,
            "operation_id":operation_id,"action":action,
            "principal_id":"test-operator-1",
            "authentication_event_id":"test-idp-session-000001",
        }


class TestOperationsPure(unittest.TestCase):
    def test_policy_bounds_and_nontrivial_thresholds(self):
        policy=MonitoringPolicy()
        self.assertLess(policy.queue_warn_seconds,policy.queue_critical_seconds)
        for key,bad in (
            ("queue_warn_seconds",True),
            ("queue_warn_seconds",20),
            ("queue_critical_seconds",3.2),
            ("inbox_critical_seconds",None),
            ("metric_stale_seconds",0),
            ("outbox_warn_seconds",2592001),
        ):
            with self.subTest(key=key,bad=bad),self.assertRaises(ContractError):
                MonitoringPolicy(**{key:bad})
        with self.assertRaises(ContractError):
            MonitoringPolicy(queue_warn_seconds=100,
                             queue_critical_seconds=100)

    def test_empty_project_has_no_false_healthy_score_or_alert(self):
        self.assertEqual(alerts_for(_metrics(),MonitoringPolicy()),())
        self.assertEqual(OPS_VERSION,"group-c-operations/v1")

    def test_queue_backlog_warn_and_critical_exact_edges(self):
        p=MonitoringPolicy()
        m=_metrics()
        m.update(queued_jobs=1,oldest_queue_seconds=p.queue_warn_seconds-1)
        self.assertEqual(alerts_for(m,p),())
        m["oldest_queue_seconds"]=p.queue_warn_seconds
        self.assertEqual(alerts_for(m,p)[0].severity,"warning")
        m["oldest_queue_seconds"]=p.queue_critical_seconds
        self.assertEqual(alerts_for(m,p)[0].severity,"critical")

    def test_expired_worker_and_outbox_leases_have_distinct_responses(self):
        m=_metrics()
        m.update(expired_job_leases=1,expired_outbox_leases=2)
        alerts=alerts_for(m,MonitoringPolicy())
        self.assertEqual([a.code for a in alerts],[
            "worker_lease_expired","outbox_lease_expired_manual_ack_required",
        ])
        self.assertTrue(all(a.severity=="critical" for a in alerts))

    def test_stale_missing_source_and_failed_quality_are_not_fake_c7(self):
        p=MonitoringPolicy()
        m=_metrics()
        m.update(missing_metric_videos=2,stale_metric_videos=1,
                 max_metric_age_seconds=p.metric_critical_seconds,
                 failed_jobs=3,blocked_runs=1)
        alerts=alerts_for(m,p)
        self.assertEqual({a.code for a in alerts},{
            "worker_failed","metric_observation_missing",
            "metric_observation_stale","blocked_runs_review_required",
        })
        self.assertEqual(next(a for a in alerts
                              if a.code=="metric_observation_stale").severity,"critical")

    def test_invalid_monitor_counts_are_rejected_not_guessed(self):
        for field,bad in (("queued_jobs",True),("expired_outbox_leases",-1),
                          ("oldest_queue_seconds","300"),
                          ("failed_jobs",2.1)):
            m=_metrics()
            m[field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(ContractError):
                alerts_for(m,MonitoringPolicy())

    def test_recovery_trace_never_contains_bearer_or_signed_assertion(self):
        message=_event("recover_expired_jobs","a"*32,
                       outcome="committed",count=2,operation_id=str(uuid4()))
        self.assertEqual(message["version"],OPS_VERSION)
        self.assertNotIn("project_id",message)
        self.assertNotIn("bearer",message)
        self.assertNotIn("signed_assertion",message)
        self.assertEqual(len(message["project_fingerprint"]),16)

    def test_typed_operational_receipts_immutable(self):
        alert=OperationalAlert("worker_lease_expired","critical",1)
        with self.assertRaises(FrozenInstanceError):
            alert.code="different"
        receipt=RecoveryReceipt(
            operation_id=str(uuid4()),project_id="b"*32,
            recovered_job_ids=(1,),recovered_count=1,
            request_sha256="a"*64,authority_sha256="b"*64,replayed=False,
        )
        with self.assertRaises(FrozenInstanceError):
            receipt.recovered_count=999

    def test_observer_rejected_type(self):
        with self.assertRaises(ContractError):
            PostgreSQLGroupCOperations(observer="not-a-callback")


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "Requires explicit disposable PG17 for ops/recovery integration",
)
class TestOperationsPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        TestDurableWorkerPG17.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_durable_worker import TestDurableWorkerPG17
        self.base=TestDurableWorkerPG17()
        self.base.conn=self.conn
        self.base.psycopg=self.psycopg
        self.base.setUp()
        self.project=self.base.project
        self.ops=PostgreSQLGroupCOperations(
            policy=MonitoringPolicy(metric_stale_seconds=60,
                                    metric_critical_seconds=86400),
            worker_policy=self.base.worker.policy,
        )
        self.admin=_TestRecoveryIAM()

    def tearDown(self):
        self.base.tearDown()

    def test_project_pulse_counts_real_queued_jobs_and_pending_state(self):
        pulse=self.ops.snapshot(self.conn,project_id=self.project)
        self.assertEqual(pulse.project_id,self.project)
        self.assertEqual(pulse.queued_jobs,1)
        self.assertEqual(pulse.running_jobs,0)
        self.assertEqual(pulse.expired_job_leases,0)
        self.assertEqual(pulse.pending_outbox,0)
        self.assertEqual(pulse.recent_recovery_operations,0)
        self.assertEqual(pulse.version,OPS_VERSION)
        self.assertIsInstance(pulse.alerts,tuple)

    def test_expired_lease_recovery_fences_old_worker_and_audits_idempotently(self):
        lease=self.base.claim()
        self.conn.execute(
            "UPDATE group_c_jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(lease.job_id,),
        )
        before=self.ops.snapshot(self.conn,project_id=self.project)
        self.assertEqual(before.expired_job_leases,1)
        self.assertIn("worker_lease_expired",[a.code for a in before.alerts])
        op_id=str(uuid4())
        first=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=op_id,
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        self.assertEqual(first.recovered_count,1)
        self.assertEqual(first.recovered_job_ids,(lease.job_id,))
        self.assertFalse(first.replayed)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_jobs WHERE id=%s",
            (lease.job_id,),
        ).fetchone()[0],"queued")
        with self.assertRaises(ContractError) as caught:
            self.base.worker.heartbeat(self.conn,lease=lease)
        self.assertEqual(caught.exception.code,"worker_lease_lost")
        again=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=op_id,
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        self.assertTrue(again.replayed)
        self.assertEqual(again.recovered_job_ids,first.recovered_job_ids)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_recovery_operations WHERE id=%s::uuid",
            (op_id,),
        ).fetchone()[0],1)

    def test_nonexpired_active_worker_unaffected(self):
        lease=self.base.claim()
        receipt=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=str(uuid4()),
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        self.assertEqual(receipt.recovered_count,0)
        self.assertEqual(self.conn.execute(
            "SELECT lease_token FROM group_c_jobs WHERE id=%s",
            (lease.job_id,),
        ).fetchone()[0],lease.token)
        self.assertEqual(self.ops.snapshot(self.conn,project_id=self.project).running_jobs,1)

    def test_failed_last_attempt_stays_terminal_after_recovery(self):
        job,_=self.base.ids[0]
        self.conn.execute(
            "UPDATE group_c_jobs SET max_attempts=1 WHERE id=%s",(job,),
        )
        lease=self.base.claim()
        self.conn.execute(
            "UPDATE group_c_jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(job,),
        )
        result=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=str(uuid4()),
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        self.assertEqual(result.recovered_count,1)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_jobs WHERE id=%s",(job,),
        ).fetchone()[0],"failed")
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE id=%s::uuid",
            (lease.run_id,),
        ).fetchone()[0],"failed")

    def test_wrong_project_cannot_recover_or_observe_its_jobs(self):
        alien=uuid4().hex
        self.assertEqual(self.ops.snapshot(self.conn,project_id=alien).queued_jobs,0)
        with self.assertRaises(self.psycopg.Error):
            self.ops.recover_expired_jobs(
                self.conn,project_id=alien,operation_id=str(uuid4()),
                signed_assertion="synthetic-admin-recovery-assertion-token",
                verifier=self.admin,
            )
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_jobs WHERE project_id=%s LIMIT 1",
            (self.project,),
        ).fetchone()[0],"queued")

    def test_unverified_admin_never_mutates_expired_lease(self):
        lease=self.base.claim()
        self.conn.execute(
            "UPDATE group_c_jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",(lease.job_id,),
        )
        with self.assertRaises(ContractError) as err:
            self.ops.recover_expired_jobs(
                self.conn,project_id=self.project,operation_id=str(uuid4()),
                signed_assertion="synthetic-admin-recovery-assertion-token",
                verifier=_TestRecoveryIAM(allowed=False),
            )
        self.assertEqual(err.exception.code,"recovery_auth_unavailable")
        self.assertEqual(self.ops.snapshot(self.conn,project_id=self.project).expired_job_leases,1)

    def test_cross_project_idempotency_collision_fails_with_audit_atomicity(self):
        opid=str(uuid4())
        self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=opid,
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        # Keep same project, but another verifier identity to force
        # authority hash conflict, without exposing a foreign project.
        class Other(_TestRecoveryIAM):
            def verify_recovery(self,*args,**kwargs):
                row=super().verify_recovery(*args,**kwargs)
                if row:row["authentication_event_id"]="different-auth-event-123"
                return row
        with self.assertRaises(ContractError) as err:
            self.ops.recover_expired_jobs(
                self.conn,project_id=self.project,operation_id=opid,
                signed_assertion="synthetic-admin-recovery-assertion-token",
                verifier=Other(),
            )
        self.assertEqual(err.exception.code,"recovery_operation_conflict")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_recovery_operations WHERE id=%s",
            (opid,),
        ).fetchone()[0],1)

    def test_immutable_recovery_audit_is_not_deletable(self):
        result=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=str(uuid4()),
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "DELETE FROM group_c_recovery_operations WHERE id=%s::uuid",
                (result.operation_id,),
            )

    def test_http_ops_read_and_recovery_are_separately_authorized(self):
        import psycopg
        api=GroupCHTTPAPI(
            connect=lambda:psycopg.connect(os.getenv("CM_TEST_PG_DSN"),
                                            autocommit=True),
            access_verifier=_TestIAM(scope=self.project),
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
            recovery_verifier=self.admin,
        )
        endpoint="/api/group-c/v1/projects/"+self.project
        status,payload,_=_call(api,_env(path=endpoint+"/operations/health"))
        self.assertEqual(status,200)
        self.assertEqual(payload["data"]["queued_jobs"],1)
        self.assertEqual(payload["schema_version"],"group-c-http/v1")
        status,payload,_=_call(api,_env(
            "POST",endpoint+"/operations/recover",
            origin=ORIGIN,csrf="test-verified-csrf-"+("y"*32),
            body={"operation_id":str(uuid4()),
                  "signed_assertion":"synthetic-admin-recovery-assertion-token"},
        ))
        self.assertEqual(status,200)
        self.assertEqual(payload["data"]["recovered_count"],0)

    def test_unavailable_recovery_verifier_http_never_mutates(self):
        import psycopg
        api=GroupCHTTPAPI(
            connect=lambda:psycopg.connect(os.getenv("CM_TEST_PG_DSN"),
                                            autocommit=True),
            access_verifier=_TestIAM(scope=self.project),
            allowed_hosts=(HOST,),allowed_origins=(ORIGIN,),
        )
        endpoint="/api/group-c/v1/projects/"+self.project
        status,payload,_=_call(api,_env(
            "POST",endpoint+"/operations/recover",
            origin=ORIGIN,csrf="test-verified-csrf-"+("y"*32),
            body={"operation_id":str(uuid4()),
                  "signed_assertion":"synthetic-admin-recovery-assertion-token"},
        ))
        self.assertEqual(status,503)
        self.assertEqual(payload["error"]["code"],"recovery_auth_unavailable")

    def test_outbox_leases_report_only_no_blind_reclaim(self):
        # Outbox is distinct from job leases: external consumer ACK may have
        # committed, so ops must NOT make it pending or delivered.
        # The DB sample is a completed certified C7 from existing fixtures.
        from test_group_c_durable_worker import _SyntheticC7IAM
        request=self.base.request()
        lease=self.base.claim()
        result=self.base.worker.execute_score(
            self.conn,lease=lease,review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )
        oid=result.analytics_outbox_id
        self.conn.execute(
            """UPDATE group_c_outbox SET state='leased',lease_token=%s::uuid,
                      lease_until=clock_timestamp()-interval '1 second'
               WHERE id=%s""",(str(uuid4()),oid),
        )
        pulse=self.ops.snapshot(self.conn,project_id=self.project)
        self.assertEqual(pulse.expired_outbox_leases,1)
        self.assertIn("outbox_lease_expired_manual_ack_required",
                      [a.code for a in pulse.alerts])
        receipt=self.ops.recover_expired_jobs(
            self.conn,project_id=self.project,operation_id=str(uuid4()),
            signed_assertion="synthetic-admin-recovery-assertion-token",
            verifier=self.admin,
        )
        self.assertEqual(receipt.recovered_count,0)
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_outbox WHERE id=%s",(oid,),
        ).fetchone()[0],"leased")

    def test_migration_063_checksum_repeat_apply(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        source=Path(__file__).resolve().parents[1] / "intelligence/migrations/063_group_c_monitoring_recovery.sql"
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (source.name,),
        ).fetchone()[0],sha)
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
