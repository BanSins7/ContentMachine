"""Stage14 contract tests for fail-closed release gate, no PostgreSQL required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import psi_release_gate as gate


class ReleaseGateContracts(unittest.TestCase):
    def test_required_versions_are_exactly_030_through_041(self):
        self.assertEqual(gate.PSI_VERSIONS,[f"{i:03d}" for i in range(30,42)])
        self.assertEqual(len(gate.PSI_VERSIONS),12)

    def test_file_manifest_matches_checkout(self):
        files=gate.manifest()
        self.assertGreaterEqual(len(files),12)
        for version in gate.PSI_VERSIONS:
            self.assertEqual(sum(k[:3]==version and
                                 k not in gate.KNOWN_PARALLEL_STAGE_FILES
                                 for k in files),1)
        for parallel in gate.KNOWN_PARALLEL_STAGE_FILES:
            self.assertIn(parallel, files)
        self.assertEqual(len(files), len(list(gate.MIGRATIONS.glob("[0-9]*.sql"))))

    def test_missing_stage_or_duplicate_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in range(30,42):
                (root / (f"{n:03d}_file.sql")).write_text("-- sample")
            with patch.object(gate,"MIGRATIONS",root),patch.object(gate,"REQUIRED",[]):
                self.assertEqual(len(gate.manifest()),12)
                (root/"041_file.sql").unlink()
                with self.assertRaisesRegex(gate.ReleaseBlocked,"Missing/duplicate"):
                    gate.manifest()
                (root/"041_file.sql").write_text("-- sample")
                (root/"041_duplicate.sql").write_text("-- duplicate")
                with self.assertRaisesRegex(gate.ReleaseBlocked,"Missing/duplicate"):
                    gate.manifest()

    def test_sql_checksum_exactly_matches_migrator_on_crlf_and_lf(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for n in range(30,42):
                (root/f"{n:03d}_file.sql").write_bytes(
                    b"--line one\r\nSELECT 1;\r\n")
            with patch.object(gate,"MIGRATIONS",root),patch.object(gate,"REQUIRED",[]):
                expected=hashlib.sha256(b"--line one\nSELECT 1;\n").hexdigest()
                checksum=gate.manifest()
                self.assertTrue(all(value==expected for value in checksum.values()))
                for p in root.iterdir():
                    p.write_bytes(b"--line one\nSELECT 1;\n")
                self.assertEqual(gate.manifest(),checksum)

    def test_missing_file_blocks_technical_release(self):
        with patch.object(gate,"REQUIRED",["docs/DOES_NOT_EXIST_STAGE14.md"]):
            with self.assertRaisesRegex(gate.ReleaseBlocked,"files absent"):
                gate.manifest()

    def test_no_postgres_cannot_pass_technical_gate(self):
        with patch.dict("os.environ",{},clear=True):
            with patch.object(gate,"legacy_unchanged",return_value="known_baseline"):
                with self.assertRaisesRegex(gate.ReleaseBlocked,"CM_TEST_PG_DSN required"):
                    gate.run(strict_ci=True)
                state=gate.run(strict_ci=False)
                self.assertEqual(state["technical_gate"],"partial")
                self.assertEqual(state["database"],"NOT_VERIFIED")
                self.assertIn("HUMAN",state["merge_approval"])

    def test_no_git_verification_cannot_be_a_green_gate(self):
        with patch.dict("os.environ",{},clear=True):
            state=gate.run(strict_ci=False,enforce_git=False)
            self.assertEqual(state["technical_gate"],"partial")
            self.assertEqual(state["legacy_migrations"],"NOT_VERIFIED")


if __name__=="__main__":
    unittest.main()
