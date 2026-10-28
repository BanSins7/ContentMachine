"""GB-MOD-29 source-only release audit and strict external QA trust tests."""
from __future__ import annotations

from dataclasses import replace
import unittest

from intelligence.group_b.release import (
    CORE_MAIN_SHA, ExternalReleaseEvidence,ReleaseState,
    audit_scope,gate_release,
)

MAIN=CORE_MAIN_SHA
HEAD="f"*40
PATHS=[
  ".github/workflows/group-b-module.yml",
  "app.py","site/index.html","site/projects.js",
  "site/group-b.js","site/group-b.css",
  "intelligence/group_b/__init__.py",
  "intelligence/group_b/release.py",
  "intelligence/migrations/055_group_b_runtime.sql",
  "intelligence/migrations/056_group_b_qdp_receipts.sql",
  "intelligence/migrations/057_group_b_budget_ledger.sql",
  "intelligence/migrations/058_group_b_worker_jobs.sql",
  "intelligence/migrations/059_group_b_observability.sql",
  "tests/fixtures/group_b_golden_matrix_v1.json",
  "tests/test_group_b_release_db.py",
  "tests/test_group_b_ui.cjs",
  "scripts/group_b_qa.py","scripts/group_b_premerge.py",
  "docs/GROUP_B_RELEASE.md",
]


def evidence():
    return ExternalReleaseEvidence(
        group_b_head_sha=HEAD,base_main_sha=MAIN,
        linux_qa_run_id="123",windows_qa_run_id="234",
        pg17_qa_run_id="345",
        qa_artifact_sha256s=("a"*64,"b"*64,"c"*64),
        peer_group_a_sha="1"*40,peer_qdp_sha="2"*40,
        peer_group_c_sha="3"*40,
        peer_unified_analytics_sha="4"*40,
        source_rights_gold_evidence_sha256="d"*64,
        actual_model_quality_receipt_sha256="e"*64,
        full_seven_port_uae_e2e_receipt_sha256="f"*64,
    )


class FakeSigner:
    def __init__(self,allowed):
        self.allowed=allowed
        self.calls=0
    def verify_real_release(self,main,head,claims):
        self.calls+=1
        return self.allowed is True and claims.group_b_head_sha==head


class TestB29ReleaseGates(unittest.TestCase):
    def test_owned_module_migrations_and_minimal_app_site_paths(self):
        s=audit_scope(PATHS,main_sha=MAIN,group_b_sha=HEAD,
                      verified_git_diff=True)
        self.assertEqual(s.state,ReleaseState.AUDITED)
        self.assertFalse(s.can_merge)
        self.assertFalse(s.production_eligible)
        self.assertEqual(s.blocking,())

    def test_unrelated_qdp_a_c_analytics_source_or_main_sql005_denied(self):
        for extra in (
            "intelligence/group_a/core.py",
            "intelligence/group_c/engine.py",
            "intelligence/unified_analytics/group_b_adapter.py",
            "intelligence/migrations/005_content_features_b.sql",
            "intelligence/migrations/055_group_a_handoff_inbox.sql",
            "intelligence/migrations/060_group_b_experimental.sql",
            "site/other-feature.js",
            "../main.py",
        ):
            with self.subTest(path=extra):
                s=audit_scope(PATHS+[extra],main_sha=MAIN,
                              group_b_sha=HEAD,verified_git_diff=True)
                self.assertEqual(s.state,ReleaseState.BLOCKED)
                self.assertFalse(s.can_merge)
                self.assertIn("foreign_module_or_unlisted_path_changed",
                              s.blocking)

    def test_invalid_git_scope_no_empty_or_forged_authority(self):
        for changes,flags in (([],{}),(PATHS,{}),(PATHS,{"deleted_paths":["app.py"]}),
                              (PATHS+["app.py"],{})):
            v=audit_scope(changes,main_sha=MAIN,group_b_sha=HEAD,**flags)
            self.assertEqual(v.state,ReleaseState.BLOCKED)

    def test_draft_pr_not_ready_even_with_self_claimed_green_status(self):
        scope=audit_scope(PATHS,main_sha=MAIN,group_b_sha=HEAD,
                          verified_git_diff=True)
        for proof,host in ((None,None),(evidence(),None),
                           (evidence(),FakeSigner(False))):
            with self.subTest(proof=proof is not None,host=host is not None):
                d=gate_release(scope,proof,trusted_attestor=host)
                self.assertIs(d.state,ReleaseState.BLOCKED)
                self.assertFalse(d.can_merge)
                self.assertTrue(d.blocking)

    def test_stale_sha_or_untrusted_peer_ref_blocks_even_with_test_signer(self):
        scope=audit_scope(PATHS,main_sha=MAIN,group_b_sha=HEAD,
                          verified_git_diff=True)
        d=gate_release(scope,replace(evidence(),group_b_head_sha="0"*40),
                       trusted_attestor=FakeSigner(True))
        self.assertFalse(d.can_merge)
        self.assertIn("evidence_not_pinned_to_current_branch_and_main",
                      d.blocking)
        d=gate_release(scope,replace(evidence(),peer_group_a_sha="no_sha"),
                       trusted_attestor=FakeSigner(True))
        self.assertFalse(d.can_merge)
        self.assertIn("peer_commit_evidence_invalid",d.blocking)

    def test_externally_authentic_real_release_is_separate_future_gate(self):
        # Positive unit proves that only a trusted attestor can move the
        # state; this fixture DOES NOT prove a real model was evaluated.
        scope=audit_scope(PATHS,main_sha=MAIN,group_b_sha=HEAD,
                          verified_git_diff=True)
        host=FakeSigner(True)
        result=gate_release(scope,evidence(),trusted_attestor=host)
        self.assertEqual(host.calls,1)
        self.assertIs(result.state,ReleaseState.READY)
        self.assertTrue(result.can_merge)


if __name__=="__main__":
    unittest.main()
