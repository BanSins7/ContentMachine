"""GC-MOD-23: pure exact quality classification and disposable PG17 failures.

No source mocking in production; test-only fixture generates raw snapshots,
QDP-like already-accepted run and separately verified synthetic human review.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.contracts import ContractError
from intelligence.group_c.metric_quality_gate import (
    QUALITY_VERSION, QualityDecision, PersistedQualityReceipt,
    approved_growth_limit, classify_quality_failure, reject_anomalous_growth,
)

T=datetime(2026,10,20,12,0,tzinfo=timezone.utc)


class TestQualityGatePure(unittest.TestCase):
    def test_explicit_counter_correction_not_negative_clamped(self):
        d=classify_quality_failure(ContractError(
            "counter_decreased","Raw current views below previous"))
        self.assertEqual(d.calculation_status,"counter_correction")
        self.assertEqual(d.score_status,"counter_correction")
        self.assertFalse(hasattr(d,"breakout_score"))

    def test_incompatible_versions_and_source_have_distinct_reason_codes(self):
        for code in ("incompatible_metric_definition","incompatible_metric_version",
                     "metric_history_changed","source_policy_unavailable",
                     "source_policy_expired","publication_expired",
                     "source_expired_or_retracted","metric_snapshot_ineligible"):
            with self.subTest(code=code):
                result=classify_quality_failure(ContractError(code,"source mismatch"))
                self.assertEqual(result.score_status,"incompatible_source")
                self.assertEqual(result.reason_code,code)

    def test_missing_counters_zero_views_and_insufficient_peers(self):
        for code in ("missing_raw_counter","zero_views_rate_undefined",
                     "creator_cohort_not_approved","prior_cohort_not_approved",
                     "incompatible_prior_peer","insufficient_peer_count"):
            with self.subTest(code=code):
                self.assertEqual(
                    classify_quality_failure(ContractError(code,"missing")).score_status,
                    "insufficient_data",
                )

    def test_invalid_timestamp_and_publication(self):
        for code in ("invalid_observation_interval","invalid_publication_timestamp",
                     "invalid_timestamp"):
            with self.subTest(code=code):
                self.assertEqual(
                    classify_quality_failure(ContractError(code,"bad time")).score_status,
                    "invalid_timestamp",
                )

    def test_config_and_human_approval_are_not_invented(self):
        for code in ("config_not_activated","review_not_approved",
                     "review_subject_changed","invalid_growth_policy"):
            with self.subTest(code=code):
                self.assertEqual(
                    classify_quality_failure(ContractError(code,"unapproved")).score_status,
                    "pending_configuration",
                )

    def test_unrelated_iam_and_lease_failures_do_not_become_quality(self):
        for code in ("human_auth_unavailable","worker_lease_lost",
                     "revision_conflict","transaction_contract_unavailable",
                     "invalid_worker_option"):
            with self.subTest(code=code):
                self.assertIsNone(classify_quality_failure(
                    ContractError(code,"not metric evidence")))
        self.assertIsNone(classify_quality_failure(RuntimeError("database offline")))

    def test_invalid_quality_status_and_immutable_receipt(self):
        d=QualityDecision("growth_anomaly","calculation_error",
                          "calculation_error","anomaly")
        self.assertEqual(d.schema_version,QUALITY_VERSION)
        with self.assertRaises(FrozenInstanceError):
            d.reason_code="changed"
        for attempt in (
            ("complete","complete","blocked"),
            ("counter_correction","complete","blocked"),
            ("incompatible_source","incompatible_source","anomaly"),
            ("calculation_error","calculation_error","anomaly"),
        ):
            with self.subTest(attempt=attempt),self.assertRaises(ContractError):
                QualityDecision("missing_raw_counter",*attempt)

    def test_missing_growth_threshold_never_guessed(self):
        self.assertIsNone(approved_growth_limit({}))
        self.assertIsNone(approved_growth_limit({"max_anomaly_zscore":"999"}))
        value=reject_anomalous_growth(
            growth_policy={},previous_views=1,
            current_views=1_000_000_001,
            previous_observed_at=T,current_observed_at=T+timedelta(hours=1),
        )
        self.assertEqual(value,Decimal("1000000000.000000000000"))

    def test_growth_limit_must_be_reviewed_exact_string_no_float(self):
        for bad in (None,True,1,2.0,"-5","NaN","Infinity","0",
                    "1e9","0.000000000000","0.1234567890123","01"," 5"):
            with self.subTest(bad=bad),self.assertRaises(ContractError):
                approved_growth_limit({"max_views_growth_per_hour":bad})
        with self.assertRaises(ContractError):
            approved_growth_limit(None)

    def test_independent_exact_growth_gold_and_reviewed_threshold(self):
        kwargs=dict(previous_views=100,current_views=600,
                    previous_observed_at=T,
                    current_observed_at=T+timedelta(hours=2))
        exact=reject_anomalous_growth(
            growth_policy={"max_views_growth_per_hour":"250.000000000000"},
            **kwargs)
        self.assertEqual(exact,Decimal("250.000000000000"))
        with self.assertRaises(ContractError) as error:
            reject_anomalous_growth(
                growth_policy={"max_views_growth_per_hour":"249.999999999999"},
                **kwargs)
        self.assertEqual(error.exception.code,"growth_anomaly")
        decision=classify_quality_failure(error.exception)
        self.assertEqual(decision.severity,"anomaly")
        self.assertEqual(decision.score_status,"calculation_error")

    def test_decimal_math_ignores_external_low_precision(self):
        kwargs=dict(previous_views=0,current_views=100,
                    previous_observed_at=T,
                    current_observed_at=T+timedelta(hours=3))
        with localcontext() as ctx:
            ctx.prec=5
            ctx.rounding=ROUND_DOWN
            self.assertEqual(reject_anomalous_growth(
                growth_policy={"max_views_growth_per_hour":"33.333333333334"},
                **kwargs),Decimal("33.333333333333"))

    def test_approved_threshold_numeric_30_12_boundary(self):
        self.assertEqual(approved_growth_limit(
            {"max_views_growth_per_hour":"0.000000000001"}),
            Decimal("0.000000000001"))
        self.assertEqual(approved_growth_limit(
            {"max_views_growth_per_hour":"999999999999999999.999999999999"}),
            Decimal("999999999999999999.999999999999"))
        with self.assertRaises(ContractError):
            approved_growth_limit(
                {"max_views_growth_per_hour":"9999999999999999999.000000000000"})


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB")=="YES",
    "GC23 requires explicitly disposable PG17 and GC_TEST_DISPOSABLE_DB=YES",
)
class TestQualityGatePG17(unittest.TestCase):
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
        self.fixture=TestDurableWorkerPG17()
        self.fixture.conn=self.conn
        self.fixture.psycopg=self.psycopg
        self.fixture.setUp()
        self.project=self.fixture.project
        self.video=self.fixture.target[0]

    def tearDown(self):
        self.fixture.tearDown()

    def _approved(self,*,anomaly=None):
        if anomaly is None:
            return self.fixture.request()
        config=self.conn.execute(
            """INSERT INTO metric_calculation_configs(
                  calculation_version,alpha,bayesian_k,growth_policy,is_active)
               VALUES(%s,5,50,%s::jsonb,FALSE) RETURNING id""",
            ("gc23-"+uuid4().hex,
             '{"max_views_growth_per_hour":"'+str(anomaly)+'"}'),
        ).fetchone()[0]
        request=self.fixture.fixture.signed(config_id=config)
        self.fixture.fixture.activate(request,config_id=config)
        self.fixture.config=config
        self.conn.execute(
            "UPDATE group_c_runs SET calculation_config_id=%s "
            "WHERE project_id=%s AND state='queued'",
            (config,self.project),
        )
        return request

    def _run(self,request):
        from test_group_c_durable_worker import _SyntheticC7IAM
        return self.fixture.worker.run_once(
            self.conn,project_id=self.project,worker_id="node-001",
            review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(),
        )

    def _check(self,finished,reason,expected_status,expected_severity="blocked"):
        self.assertEqual(finished.state,"failed")
        self.assertIsNotNone(finished.quality_report)
        self.assertIsInstance(finished.quality_report,PersistedQualityReceipt)
        self.assertEqual(finished.quality_report.reason_code,reason)
        row=self.conn.execute(
            """SELECT q.reason_code,q.calculation_status,q.score_status,
                      q.severity,r.state,j.state,r.calculated_metric_id,
                      q.fingerprint_sha256
               FROM group_c_quality_reports q
               JOIN group_c_runs r ON r.id=q.run_id
               JOIN group_c_jobs j ON j.id=q.job_id
               WHERE q.id=%s""",
            (finished.quality_report.report_id,),
        ).fetchone()
        self.assertEqual(row[:6],(reason,expected_status,expected_status,
                                 expected_severity,"blocked","failed"))
        self.assertIsNone(row[6])
        self.assertEqual(row[7],finished.quality_report.fingerprint_sha256)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM calculated_metrics WHERE post_id=%s",
            (self.video,),
        ).fetchone()[0],0)

    def _new_current(self,*,views=150,likes=12,comments=4,shares=2,
                     metric_version="v1",quality="ok"):
        sid=self.conn.execute(
            """INSERT INTO metric_snapshots(
                  video_id,source_id,source_policy_version_id,
                  observed_at,collected_at,views,likes,comments,shares,
                  source_metric_definition,source_metric_version,
                  precision,data_quality_status,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '1 hour',
                      now()-interval '30 minute',%s,%s,%s,%s,
                      'platform_post_views',%s,'exact',%s,FALSE)
               RETURNING id""",
            (self.video,self.fixture.source,self.fixture.policy,
             views,likes,comments,shares,metric_version,quality),
        ).fetchone()[0]
        self.conn.execute(
            """UPDATE group_c_runs
               SET snapshot_id=%s,previous_snapshot_id=%s
               WHERE project_id=%s AND state='queued'""",
            (sid,self.fixture.target[1],self.project),
        )
        return sid

    def test_complete_safe_score_does_not_create_quality_report(self):
        request=self._approved()
        from intelligence.group_c.durable_worker import WorkerCompletion
        result=self._run(request)
        self.assertIsInstance(result,WorkerCompletion)
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM group_c_quality_reports WHERE project_id=%s",
            (self.project,),
        ).fetchone())
        self.assertEqual(self.conn.execute(
            "SELECT score_status FROM calculated_metrics WHERE id=%s",
            (result.score.calculated_metric_id,),
        ).fetchone()[0],"complete")

    def test_missing_required_share_counter_blocked_insufficient(self):
        request=self._approved()
        sid=self._new_current(shares=None)
        original=self.conn.execute(
            "SELECT shares,views FROM metric_snapshots WHERE id=%s",
            (sid,),
        ).fetchone()
        self.assertEqual(original,(None,150))
        self._check(self._run(request),"missing_raw_counter","insufficient_data")
        self.assertEqual(self.conn.execute(
            "SELECT shares,views FROM metric_snapshots WHERE id=%s",
            (sid,),
        ).fetchone(),original)

    def test_corrected_counter_does_not_become_zero_growth(self):
        request=self._approved()
        sid=self._new_current(views=50,quality="counter_correction")
        self._check(self._run(request),"counter_decreased","counter_correction")
        self.assertEqual(self.conn.execute(
            "SELECT views FROM metric_snapshots WHERE id=%s",(sid,),
        ).fetchone()[0],50)

    def test_incompatible_source_metric_version_blocked(self):
        request=self._approved()
        self._new_current(metric_version="v2")
        self._check(self._run(request),"incompatible_metric_version","incompatible_source")

    def test_source_right_revoked_before_score_is_incompatible(self):
        request=self._approved()
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.fixture.source,),
        )
        result=self._run(request)
        self.assertEqual(result.state,"failed")
        self.assertIsNotNone(result.quality_report)
        self.assertEqual(result.quality_report.score_status,"incompatible_source")
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM calculated_metrics WHERE post_id=%s",(self.video,),
        ).fetchone())

    def test_invalid_time_order_explicit_status_no_zero_values(self):
        request=self._approved()
        self.conn.execute(
            """UPDATE group_c_runs SET snapshot_id=%s,
                     previous_snapshot_id=%s
               WHERE project_id=%s AND state='queued'""",
            (self.fixture.previous,self.fixture.target[1],self.project),
        )
        self._check(self._run(request),"invalid_observation_interval","invalid_timestamp")

    def test_human_review_required_no_auto_generated_config(self):
        # Stage11 pending review is not an authorization; its reason is
        # allowed as diagnostic but IAM is NEVER bypassed.
        from intelligence.group_c.human_review import PostgreSQLHumanReview
        creator,prior=self.fixture.fixture.freeze()
        pending=PostgreSQLHumanReview().create_request(
            self.conn,project_id=self.project,config_id=self.fixture.config,
            creator_freeze_id=creator.id,prior_freeze_id=prior.id,
            minimum_creator_posts=2,minimum_prior_posts=2,
        )
        self._check(self._run(pending),"review_not_approved","pending_configuration")

    def test_approved_growth_threshold_detects_excess_rate(self):
        request=self._approved(anomaly="1.000000000000")
        result=self._run(request)
        self._check(result,"growth_anomaly","calculation_error","anomaly")
        self.assertEqual(self.conn.execute(
            "SELECT views FROM metric_snapshots WHERE id=%s",
            (self.fixture.target[1],),
        ).fetchone()[0],100)

    def test_approved_growth_threshold_equal_is_not_anomaly(self):
        request=self._approved(anomaly="10.000000000000")
        from intelligence.group_c.durable_worker import WorkerCompletion
        result=self._run(request)
        self.assertIsInstance(result,WorkerCompletion)
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM group_c_quality_reports WHERE project_id=%s",
            (self.project,),
        ).fetchone())

    def test_denied_IAM_is_security_failure_not_quality(self):
        request=self._approved()
        from test_group_c_durable_worker import _SyntheticC7IAM
        result=self.fixture.worker.run_once(
            self.conn,project_id=self.project,worker_id="node-001",
            review_request_id=request.request_id,
            approval_verifier=_SyntheticC7IAM(False),
        )
        self.assertEqual(result.state,"failed")
        self.assertIsNone(result.quality_report)
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM group_c_quality_reports WHERE project_id=%s",
            (self.project,),
        ).fetchone())
        self.assertEqual(self.conn.execute(
            "SELECT state FROM group_c_runs WHERE project_id=%s",
            (self.project,),
        ).fetchone()[0],"failed")

    def test_quality_report_immutable_and_terminal_replay(self):
        request=self._approved()
        self._new_current(shares=None)
        result=self._run(request)
        self.assertIsNotNone(result.quality_report)
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "UPDATE group_c_quality_reports SET reason_code='other' WHERE id=%s",
                (result.quality_report.report_id,),
            )
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "DELETE FROM group_c_quality_reports WHERE id=%s",
                (result.quality_report.report_id,),
            )
        self.assertIsNone(self._run(request))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM group_c_quality_reports WHERE job_id=%s",
            (result.job_id,),
        ).fetchone()[0],1)

    def test_quality_report_no_forged_complete_status(self):
        request=self._approved()
        self._new_current(shares=None)
        result=self._run(request)
        with self.assertRaises(self.psycopg.Error):
            self.conn.execute(
                "INSERT INTO group_c_quality_reports("
                " project_id,run_id,video_id,job_id,source_id,reason_code,"
                " calculation_status,score_status,severity,fingerprint_sha256)"
                " VALUES(%s,%s,%s,%s,%s,'missing_raw_counter','complete',"
                " 'complete','blocked',%s)",
                (self.project,self.fixture.ids[0][1],self.video,result.job_id,
                 self.fixture.source,"a"*64),
            )

    def test_migration_version_checksum_stable_and_reapply_idempotent(self):
        import hashlib
        from pathlib import Path
        from intelligence import migrate
        p=Path(__file__).resolve().parents[1]/"intelligence/migrations/061_group_c_quality_reports.sql"
        self.assertEqual(self.conn.execute(
            "SELECT sha256 FROM schema_migrations WHERE version=%s",
            (p.name,),
        ).fetchone()[0],hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertEqual(migrate.apply_migrations(self.conn),[])


if __name__=="__main__":
    unittest.main()
