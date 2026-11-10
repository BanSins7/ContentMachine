"""Offline regression tests for the Windows Carousel Analysis launcher.

Never open a browser, start the server, install packages, or call a provider.
"""
from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts import ensure_frontend as frontend

ROOT = Path(__file__).resolve().parents[1]


class StandaloneCarouselLauncherTests(TestCase):
    def test_one_click_cmd_launcher_and_visible_backend_are_present(self):
        launcher = (ROOT / "START_CAROUSEL_ANALYSIS.bat").read_text(encoding="utf-8")
        backend = (ROOT / "scripts" / "CAROUSEL_BACKEND_CMD.bat").read_text(encoding="utf-8")
        self.assertIn('RUN_CAROUSEL_STUDIO.ps1', launcher)
        self.assertIn('"%~dp0RUN_CAROUSEL_STUDIO.ps1"', launcher)
        self.assertIn('powershell.exe -NoProfile -ExecutionPolicy Bypass', launcher)
        self.assertIn('cd /d "%~dp0.."', backend)
        self.assertIn('set "CM_NO_BROWSER=1"', backend)
        self.assertIn("python -u app.py", backend)
        self.assertIn("Ctrl+C", backend)

    def test_launcher_has_app_window_and_refuses_foreign_backend(self):
        source = (ROOT / "RUN_CAROUSEL_STUDIO.ps1").read_text(encoding="utf-8")
        for requirement in (
            "Get-StudioReadiness",
            "Test-StudioPortBusy",
            "Assert-StudioBackend",
            "server_worktree_root",
            "safe_resume_revision",
            "resume_and_dedup_supported",
            "retry_failed_supported",
            "--studio-only --strict",
            "Start-Process -FilePath $env:ComSpec",
            "CAROUSEL_BACKEND_CMD.bat",
            "--user-data-dir=",
            "--app=",
            "Start-Process $studioUrl",
        ):
            self.assertIn(requirement, source)
        self.assertIn("127.0.0.1:8765", source)
        self.assertNotIn("CM_DB_DSN=", source)
        self.assertNotIn("/api/studio/runs',", source)
        self.assertNotIn("confirm_paid_retries", source)

    def test_studio_only_strict_build_does_not_rebuild_main_frontend(self):
        called = []

        def fake_build(name, *_args, **_kwargs):
            called.append(name)
            return True

        with patch.object(frontend, "revision", return_value="abc"), \
             patch.object(frontend.shutil, "which", return_value="npm"), \
             patch.object(frontend, "build", side_effect=fake_build):
            self.assertEqual(frontend.main(["--studio-only", "--strict"]), 0)
        self.assertEqual(called, ["Carousel Studio"])

    def test_studio_only_build_failure_is_detectable_by_launcher(self):
        with patch.object(frontend, "revision", return_value="abc"), \
             patch.object(frontend.shutil, "which", return_value="npm"), \
             patch.object(frontend, "build", return_value=False):
            self.assertEqual(frontend.main(["--studio-only", "--strict"]), 1)
            # Legacy main launcher retains its best-effort behavior.
            self.assertEqual(frontend.main(["--studio-only"]), 0)

    def test_missing_node_is_an_error_only_in_strict_mode(self):
        with patch.object(frontend, "revision", return_value="abc"), \
             patch.object(frontend.shutil, "which", return_value=None):
            self.assertEqual(frontend.main(["--studio-only", "--strict"]), 1)
            self.assertEqual(frontend.main(["--studio-only"]), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
