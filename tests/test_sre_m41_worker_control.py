"""M41 cooperative stop persisted generation, no kill or unaudited reclaim."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence.relevance.worker_control import (
    WorkerControl,WorkerControlBlocked,OWNER,STOP,
)

PID="a"*32


class WorkerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        if os.name=="posix":self.directory.chmod(0o700)
        self.ctrl=WorkerControl(self.directory)

    def test_new_owner_nonce_exclusive_and_stop_no_kill(self):
        token=self.ctrl.claim(PID)
        self.assertEqual(len(token["nonce"]),32)
        self.assertEqual(self.ctrl.status()["pid"],os.getpid())
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.claim(PID)
        self.assertFalse(self.ctrl.stop_requested(token))
        with patch("intelligence.relevance.worker_control.os.kill",
                   side_effect=AssertionError("never kill PID")):
            self.assertEqual(self.ctrl.request_stop()["status"],
                             "cooperative_stop_requested")
            self.assertEqual(self.ctrl.request_stop()["status"],
                             "stop_already_requested")
        self.assertTrue(self.ctrl.stop_requested(token))
        self.ctrl.release(token)
        self.assertFalse((self.directory/OWNER).exists())
        self.assertFalse((self.directory/STOP).exists())
        self.assertEqual(self.ctrl.status()["status"],"stopped")

    def test_foreign_generation_stop_never_terminates_other_session(self):
        own=self.ctrl.claim(PID)
        self.ctrl.request_stop()
        data=json.loads((self.directory/STOP).read_text())
        data["nonce"]="b"*32
        (self.directory/STOP).write_text(json.dumps(data))
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.request_stop()
        self.assertFalse(self.ctrl.stop_requested(own))
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.release(own)

    def test_manual_stale_recovery_requires_explicit_ack_and_dead_pid(self):
        self.ctrl.claim(PID)
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.recover_stale(acknowledged=False)
        with patch("intelligence.relevance.worker_control.pid_alive",return_value=True):
            with self.assertRaises(WorkerControlBlocked):
                self.ctrl.recover_stale(acknowledged=True)
        with patch("intelligence.relevance.worker_control.pid_alive",return_value=None):
            with self.assertRaises(WorkerControlBlocked):
                self.ctrl.recover_stale(acknowledged=True)
        with patch("intelligence.relevance.worker_control.pid_alive",return_value=False):
            result=self.ctrl.recover_stale(acknowledged=True)
        self.assertEqual(result["status"],"stale_owner_cleared_manual_audit_required")
        self.assertFalse((self.directory/OWNER).exists())

    def test_no_spoofed_unsafe_control_dir_or_symlink(self):
        with tempfile.TemporaryDirectory() as other:
            if os.name=="posix":
                os.chmod(other,0o777)
                with self.assertRaises(WorkerControlBlocked):
                    WorkerControl(other)
        link=self.directory/"link"
        try:link.symlink_to(self.directory, target_is_directory=True)
        except (OSError,NotImplementedError):
            return
        with self.assertRaises(WorkerControlBlocked):
            WorkerControl(link)

    def test_malformed_or_oversized_record_fail_closed(self):
        (self.directory/OWNER).write_text('{"pid":3}')
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.status()
        (self.directory/OWNER).write_text("x"*2048)
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.request_stop()

    def test_passive_status_never_creates_owner_or_stop(self):
        self.assertEqual(self.ctrl.status()["status"],"stopped")
        self.assertEqual(self.ctrl.request_stop()["status"],"not_running")
        self.assertEqual(list(self.directory.iterdir()),[])

    def test_orphan_stop_requires_explicit_operator_recovery(self):
        own=self.ctrl.claim(PID)
        self.ctrl.request_stop()
        (self.directory/OWNER).unlink()
        with self.assertRaises(WorkerControlBlocked):
            self.ctrl.claim(PID)
        self.assertEqual(self.ctrl.status()["status"],"orphan_stop_recovery_required")
        result=self.ctrl.recover_stale(acknowledged=True)
        self.assertEqual(result["status"],"orphan_stop_cleared")
        self.assertFalse((self.directory/STOP).exists())


if __name__=="__main__":
    unittest.main()
