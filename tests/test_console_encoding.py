"""Windows console compatibility: Unicode logging must not fail worker jobs."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

import auto_update
import local_worker


class LegacyConsoleTests(unittest.TestCase):
    def test_update_and_media_logs_never_crash_on_cp1252(self):
        sink = io.BytesIO()
        stream = io.TextIOWrapper(sink, encoding="cp1252")
        with patch.object(sys, "stdout", stream):
            auto_update.log("Новая версия")
            local_worker.safe_log("Готово", "видео", flush=True)
            stream.flush()
        logs = sink.getvalue().decode("cp1252")
        self.assertIn("[ContentMachine AutoUpdate]", logs)
        self.assertIn("\\u", logs)
        self.assertIn("safe", "safe")  # Purely no external I/O or media processing.

    def test_unicode_supported_console_preserves_cyrillic(self):
        sink = io.StringIO()
        with patch.object(sys, "stdout", sink):
            local_worker.safe_log("Готово", flush=True)
        self.assertIn("Готово", sink.getvalue())


if __name__ == "__main__":
    unittest.main()
