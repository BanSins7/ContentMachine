"""No remote, DB, working-branch mutation, or peer installation required."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts.qdp_peer_integration_audit import GitAuditError, analyze, inventory, DEFAULT_REFS


def command(path, *args):
    p = subprocess.run(["git", "-C", str(path), *args], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise AssertionError(p.stderr)
    return p.stdout.strip()


class PeerIntegrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        command(self.repo, "init", "-q", "-b", "main")
        command(self.repo, "config", "user.name", "QDP Test")
        command(self.repo, "config", "user.email", "qdp-test@example.invalid")
        (self.repo / "app.py").write_text("old\n")
        migrations = self.repo / "intelligence" / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "016_base.sql").write_text("SELECT 1;\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-qm", "base")
        command(self.repo, "branch", "feature/qualified-data-pipeline-v1-20261023")
        (self.repo / "app.py").write_text("new-main\n")
        command(self.repo, "commit", "-qam", "main change")
        command(self.repo, "checkout", "-q", "feature/qualified-data-pipeline-v1-20261023")
        (self.repo / "app.py").write_text("new-qdp\n")
        (migrations / "040_qdp.sql").write_text("SELECT 42;\n")
        bridge = self.repo / "intelligence" / "qualified_data" / "integrations"
        bridge.mkdir(parents=True)
        (bridge / "group_b.py").write_text('QDP_PREPARATION = "qdp-preparation/v2"\n')
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-qm", "qdp change")
        command(self.repo, "checkout", "-q", "main")
        command(self.repo, "branch", "feature/group-b-full-module")
        command(self.repo, "checkout", "-q", "feature/group-b-full-module")
        reader = self.repo / "intelligence" / "group_b" / "adapters"
        reader.mkdir(parents=True)
        (reader / "qdp.py").write_text('VERSION="qdp-preparation/v1"\n# producer_payload_mismatch\n')
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-qm", "legacy group b")
        command(self.repo, "checkout", "-q", "main")

    def test_detects_old_group_b_and_diverged_main(self):
        refs = {k: "main" for k in DEFAULT_REFS}
        refs.update(qdp="feature/qualified-data-pipeline-v1-20261023",
                    group_b="feature/group-b-full-module")
        report = analyze(self.repo, refs)
        self.assertEqual(report["release_decision"], "NO_GO")
        self.assertEqual(report["merge"]["behind"], 1)
        self.assertEqual(report["merge"]["ahead"], 1)
        self.assertIn("app.py", report["merge"]["overlapping_paths"])
        self.assertTrue(report["merge"]["merge_tree_conflict_markers"])
        self.assertEqual(report["contracts"]["group_b"], "incompatible_v1_reader")
        self.assertIn("group_b_old_event_preparation_abi", report["blockers"])

    def test_filename_checksum_collision_is_not_numeric_warning(self):
        refs = {k: "main" for k in DEFAULT_REFS}
        refs["qdp"] = "feature/qualified-data-pipeline-v1-20261023"
        report = analyze(self.repo, refs)
        self.assertFalse(report["migrations"]["conflicting_same_filename"])
        self.assertIn("040_qdp.sql", inventory(self.repo, refs["qdp"]))

    def test_missing_qdp_ref_fails_explicitly(self):
        refs = dict(DEFAULT_REFS)
        refs["qdp"] = "unknown/qdp"
        with self.assertRaisesRegex(GitAuditError, "main_and_qdp_refs_required"):
            analyze(self.repo, refs)


if __name__ == "__main__":
    unittest.main()
