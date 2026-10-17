import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_update as update


class AutoUpdateTests(unittest.TestCase):
    def test_busy_jobs(self):
        self.assertTrue(update.is_busy({'jobs':[{'status':'queued'}], 'discovery':[]}))
        self.assertTrue(update.is_busy({'jobs':[], 'discovery':[{'status':'processing'}]}))
        self.assertFalse(update.is_busy({'jobs':[{'status':'complete'}], 'discovery':[]}))

    def test_sync_does_not_change_local_edit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(update, 'ROOT', Path(tmp)):
            (Path(tmp)/'.git').mkdir()
            with patch.object(update, 'git', side_effect=['main',' M app.py']):
                with self.assertRaisesRegex(RuntimeError, 'локальные изменения'):
                    update.sync_latest()

    def test_sync_no_update(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(update, 'ROOT', Path(tmp)):
            (Path(tmp)/'.git').mkdir()
            with patch.object(update, 'git', side_effect=['main','','','abc','abc']) as mocked:
                self.assertFalse(update.sync_latest())
                self.assertEqual(mocked.call_count,5)

    def test_sync_fast_forward(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(update, 'ROOT', Path(tmp)):
            (Path(tmp)/'.git').mkdir()
            with patch.object(update, 'git', side_effect=['main','','','abc','def','','']) as mocked:
                with patch.object(update, 'validate_remote') as checked:
                    self.assertTrue(update.sync_latest())
                    checked.assert_called_once_with('def')
                self.assertEqual(mocked.call_args_list[-1].args,('merge','--ff-only','origin/main'))

    def test_reject_bad_remote_python(self):
        with patch.object(update, 'git', side_effect=['app.py', 'def broken(:\\n  pass']) as mocked:
            with self.assertRaisesRegex(RuntimeError, 'invalid Python'):
                update.validate_remote('origin/main')
            self.assertEqual(mocked.call_count, 2)


if __name__ == '__main__': unittest.main()