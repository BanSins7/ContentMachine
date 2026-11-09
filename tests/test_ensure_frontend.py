"""Offline frontend build-cache regression tests: no npm or network required."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from scripts import ensure_frontend as ui


class FrontendCacheTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "frontend"
        self.output = root / "output"
        self.source.mkdir()
        (self.source / "src").mkdir()
        (self.source / "package.json").write_text('{"scripts":{"build":"vite"}}')
        (self.source / "src" / "main.tsx").write_text("export const version=1;")
        self.external = root / "template.html"
        self.external.write_text("<h1>First</h1>")
        self.marker = self.output / ".built-commit"
        self.commands = []
        self.emit_output = True
        self.command_failure = False

    def fake_npm(self, argv, **kwargs):
        self.commands.append(tuple(argv))
        if self.command_failure:
            return SimpleNamespace(returncode=1)
        if list(argv[1:]) == ["run", "build"] and self.emit_output:
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / "index.html").write_text("built")
        return SimpleNamespace(returncode=0)

    def build(self):
        return ui.build(
            "Test", self.source, self.output, self.marker,
            "same-head", "npm", (self.external,),
        )

    def test_unmodified_sources_are_cached_but_dirty_worktree_edit_rebuilds(self):
        with patch.object(ui.subprocess, "run", side_effect=self.fake_npm):
            self.assertTrue(self.build())
            self.assertEqual(len(self.commands), 2)
            self.assertTrue(self.build())
            self.assertEqual(len(self.commands), 2)
            (self.source / "src" / "main.tsx").write_text("export const version=2;")
            self.assertTrue(self.build())
            self.assertEqual(len(self.commands), 4)

    def test_external_html_change_invalidates_bundled_template(self):
        with patch.object(ui.subprocess, "run", side_effect=self.fake_npm):
            self.assertTrue(self.build())
            self.external.write_text("<h1>Updated</h1>")
            self.assertTrue(self.build())
            self.assertEqual(len(self.commands), 4)

    def test_successful_command_without_entry_does_not_mark_build_valid(self):
        self.emit_output = False
        with patch.object(ui.subprocess, "run", side_effect=self.fake_npm):
            self.assertFalse(self.build())
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.output / ".built-sources-sha256").exists())

    def test_failed_rebuild_does_not_claim_new_revision(self):
        with patch.object(ui.subprocess, "run", side_effect=self.fake_npm):
            self.assertTrue(self.build())
            old_fingerprint = (self.output / ".built-sources-sha256").read_text()
            (self.source / "src" / "main.tsx").write_text("export const version=3;")
            self.command_failure = True
            self.assertFalse(self.build())
            self.assertEqual(old_fingerprint, (self.output / ".built-sources-sha256").read_text())
            self.command_failure = False
            self.assertTrue(self.build())
            self.assertNotEqual(old_fingerprint, (self.output / ".built-sources-sha256").read_text())


if __name__ == "__main__":
    import unittest
    unittest.main()
