"""PIE-MOD-35 preflight runner: never count skipped/unavailable PG tests as PASS."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import sys
import unittest

from scripts.pie_release_preflight import (
    observed_pg_case,_test_db_name,_receipt,_check,
)
from intelligence.patterns_insights.release_readiness import (
    ReleaseGateError,assess_standalone_readiness,
)


class PreflightSafetyTests(unittest.TestCase):
    CASE='test_true_two_connections_single_claim'
    def test_real_verbose_ok_is_observed(self):
        log='test_true_two_connections_single_claim (test_pie_release_pg17.PostgreSQL17TwoWorkerE2E.test_true_two_connections_single_claim) ... ok\n'
        self.assertTrue(observed_pg_case(log,self.CASE))
    def test_skipped_pg_case_never_promotes_pass(self):
        log='test_true_two_connections_single_claim (test_pie_release_pg17.PostgreSQL17TwoWorkerE2E.test_true_two_connections_single_claim) ... skipped "No seed"\n'
        self.assertFalse(observed_pg_case(log,self.CASE))
    def test_same_prefix_is_not_specific_test_evidence(self):
        log='test_true_two_connections_single_claim_fake (fixture.Case.test_true_two_connections_single_claim_fake) ... ok'
        self.assertFalse(observed_pg_case(log,self.CASE))
    def test_pg_log_error_and_failure_are_not_pass(self):
        for suffix in ('FAIL','ERROR','skipped missing_db'):
            self.assertFalse(observed_pg_case(
                self.CASE+' (fixture.Real) ... '+suffix,self.CASE))
    def test_other_method_cannot_attest_claim(self):
        self.assertFalse(observed_pg_case(
            'test_migration_idempotent_replay (fixture.Real) ... ok',self.CASE))
    def test_invalid_method_injection_rejected(self):
        self.assertFalse(observed_pg_case('fake (fixture) ... ok','test_(.*)'))
    def test_explicit_disposable_postgres_url(self):
        self.assertTrue(_test_db_name('postgresql://user:pass@localhost:5432/contentmachine_test'))
        self.assertTrue(_test_db_name('postgres://user@localhost/my_pie_test_17'))
    def test_production_postgres_url_rejected(self):
        for dsn in (
            'postgresql://user@localhost/contentmachine',
            'postgresql://user@localhost/postgres',
            'postgresql://user@localhost/template1',
            'postgresql://user@localhost/production',
            'https://localhost/my_test',
            'dbname=pie_test',
        ):
            self.assertFalse(_test_db_name(dsn),dsn)
    def test_successful_local_python_process_is_hash_bound(self):
        command=[sys.executable,'-c',"print('synthetic-only')"]
        state,digest,code,note,out=_check(command,20)
        self.assertEqual(state,'passed')
        self.assertEqual(code,0)
        self.assertIn('synthetic-only',out)
        self.assertEqual(digest,sha256(out.encode('utf-8')).hexdigest())
    def test_failed_local_python_process_cannot_be_green(self):
        command=[sys.executable,'-c','import sys; sys.exit(3)']
        state,digest,code,note,out=_check(command,20)
        self.assertEqual(state,'failed')
        self.assertEqual(code,3)
        self.assertEqual(note,'local_process_exit_nonzero')
    def test_silent_missing_machine_receipt_is_blocked(self):
        report=assess_standalone_readiness('e'*64,())
        self.assertEqual(report.status,'blocked_unverified')
        self.assertIn('pg17_fresh',report.missing_gates)
        self.assertIn('uae_current_rights',report.missing_gates)
    def test_even_per_test_pass_is_not_public_export_permission(self):
        receipt=_receipt('pg17_two_worker_fencing','passed','f'*64,'postgresql17_case_observed_ok')
        report=assess_standalone_readiness('e'*64,(receipt,))
        self.assertFalse(report.may_merge)
        self.assertFalse(report.may_publish)
        self.assertFalse(report.can_run_paid_model)
    def test_preflight_never_hardcodes_real_provider_token(self):
        content=(Path(__file__).resolve().parents[1]/
                 'scripts/pie_release_preflight.py').read_text('utf-8')
        self.assertNotIn('OPENROUTER_API_KEY=',content)
        self.assertNotIn('gh pr merge',content)
        self.assertNotIn('git push',content)
        self.assertIn('observed_pg_case(pg_log,method)',content)


if __name__=='__main__':
    unittest.main()
