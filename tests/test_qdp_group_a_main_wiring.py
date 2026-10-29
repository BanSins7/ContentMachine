"""Main host QDP→Group A wiring: no fake receipt, scoped events, revocation priority."""
import unittest
from unittest.mock import patch, MagicMock
from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.group_a_runtime import (
    dispatch_group_a_once, readiness, auto_dispatch_enabled,
)

P="a"*32


class TestQDPGroupAMainWiring(unittest.TestCase):
    def test_fail_closed_when_not_configured(self):
        with patch("intelligence.qualified_data.group_a_runtime._configuration",
                   side_effect=QDPError("group_a_disabled","missing",503)):
            state=readiness()
        self.assertEqual(state["state"],"unavailable")
        self.assertEqual(state["reason_code"],"group_a_disabled")
        self.assertFalse(state["receipt_means_analysis_complete"])

    def test_reject_cross_scope_and_invalid_project_before_dispatch(self):
        for bad in (True,"",P+"a","b"*31,"../etc/passwd"):
            with self.subTest(project=bad), self.assertRaises(QDPError):
                dispatch_group_a_once(project_id=bad)

    def test_readiness_requires_real_database_relations(self):
        con=MagicMock()
        con.__enter__.return_value=con
        con.execute.return_value.fetchone.return_value=(None,)*10
        with patch("intelligence.qualified_data.group_a_runtime._configuration",
                   return_value=["/absolute-media"]),patch(
                   "intelligence.qualified_data.group_a_runtime._connect_ga",
                   return_value=con):
            status=readiness()
        self.assertEqual(status["state"],"unavailable")
        self.assertEqual(status["reason_code"],"group_a_schema_missing")

    def test_revocation_always_precedes_preparation_and_is_project_scoped(self):
        revocation={"state":"acknowledged","event_id":"test"}
        with patch("intelligence.qualified_data.group_a_runtime.readiness",
                   return_value={"state":"intake_available"}),patch(
                   "intelligence.qualified_data.group_a_runtime._configuration",
                   return_value=["/root"]),patch(
                   "intelligence.qualified_data.revocation_delivery.dispatch_invalidation_once",
                   return_value=revocation) as revoke,patch(
                   "intelligence.qualified_data.outbox_delivery.dispatch_once") as prepare:
            value=dispatch_group_a_once(project_id=P)
        self.assertEqual(value["event_type"],"preparation_invalidated")
        self.assertEqual(value["result"],revocation)
        self.assertFalse(value["group_a_analysis_completed"])
        self.assertEqual(revoke.call_args.kwargs["project_id"],P)
        prepare.assert_not_called()

    def test_preparation_requires_real_receiver_when_no_invalidation(self):
        ack={"state":"acknowledged"}
        receiver=object()
        with patch("intelligence.qualified_data.group_a_runtime.readiness",
                   return_value={"state":"intake_available"}),patch(
                   "intelligence.qualified_data.group_a_runtime._configuration",
                   return_value=["/root"]),patch(
                   "intelligence.qualified_data.revocation_delivery.dispatch_invalidation_once",
                   return_value=None),patch(
                   "intelligence.qualified_data.integrations.group_a.from_installed_group_a",
                   return_value=receiver) as setup,patch(
                   "intelligence.qualified_data.outbox_delivery.dispatch_once",
                   return_value=ack) as dispatch:
            value=dispatch_group_a_once(project_id=P)
        self.assertEqual(value["event_type"],"preparation_ready")
        self.assertEqual(value["result"],ack)
        self.assertFalse(value["group_a_analysis_completed"])
        self.assertIs(dispatch.call_args.kwargs["receiver"],receiver)
        self.assertEqual(dispatch.call_args.kwargs["project_id"],P)
        self.assertEqual(dispatch.call_args.args,("group_a",))
        self.assertTrue(setup.call_args.kwargs["enabled"])

    def test_auto_dispatch_uses_explicit_operator_flag(self):
        with patch("intelligence.qualified_data.group_a_runtime.setting",
                   return_value="0"):
            self.assertFalse(auto_dispatch_enabled())
        with patch("intelligence.qualified_data.group_a_runtime.setting",
                   return_value="1"):
            self.assertTrue(auto_dispatch_enabled())


if __name__=="__main__":
    unittest.main()
