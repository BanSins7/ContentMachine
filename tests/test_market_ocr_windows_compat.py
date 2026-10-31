"""Marketing Windows OCR compatibility tests (never need real Tesseract)."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from intelligence.market_intelligence.market_ocr_compat import run_tesseract_compat
from intelligence.market_intelligence._copied.group_a.extractors.tesseract_ocr import VERSION_RE


class MarketWindowsOCRVersionTests(unittest.TestCase):
    def _run(self, cmd, stdout=b"", stderr=b""):
        done = subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=stdout, stderr=stderr)
        with patch(
            "intelligence.market_intelligence.market_ocr_compat.subprocess.run",
            return_value=done,
        ) as mocked:
            result = run_tesseract_compat(cmd, timeout=10, check=False)
            mocked.assert_called_once_with(cmd, timeout=10, check=False)
        return result

    def test_ub_mannheim_tesseract_v5_stdout(self):
        cmd = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", "--version"]
        result = self._run(cmd, stdout=b"tesseract v5.4.0.20240606\r\n libpng 1.6\r\n")
        first = result.stdout.splitlines()[0].decode("ascii")
        self.assertEqual(first, "tesseract 5.4.0.20240606")
        self.assertIsNotNone(VERSION_RE.match(first))
        self.assertIn(b" libpng 1.6", result.stdout)

    def test_ub_mannheim_tesseract_v5_stderr(self):
        cmd = ["tesseract.exe", "--version"]
        result = self._run(cmd, stderr=b"tesseract v5.4.0.20240606\n")
        self.assertEqual(result.stderr, b"tesseract 5.4.0.20240606\n")
        self.assertIsNotNone(VERSION_RE.match(result.stderr.decode().splitlines()[0]))

    def test_tsv_and_ocr_results_are_never_modified(self):
        cmd = ["tesseract.exe", r"C:\image.png", "stdout", "--psm", "6", "tsv"]
        raw = b"level\ttext\n5\ttesseract v5.4.0\n"
        result = self._run(cmd, stdout=raw)
        self.assertEqual(result.stdout, raw)

    def test_unsupported_tesseract_v4_not_disguised_as_v5(self):
        cmd = ["tesseract.exe", "--version"]
        result = self._run(cmd, stdout=b"tesseract v4.1.0\n")
        self.assertEqual(result.stdout, b"tesseract v4.1.0\n")
        self.assertIsNone(VERSION_RE.match(result.stdout.decode().splitlines()[0]))


if __name__ == "__main__":
    unittest.main()
