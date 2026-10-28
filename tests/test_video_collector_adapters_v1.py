"""VC-MOD-01: real upstream adapters are lazy and preserve owner contracts."""
from __future__ import annotations
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector.adapters.main_qv import MainQVAdapter
from intelligence.collector.adapters.dtk_provider import DTKSourceProvider
from intelligence.collector.contracts import CollectorError

P = "a" * 32
R = "e7c041e9-1234-4567-8abc-bccde0000044"


class MainQvAdapterTests(TestCase):
    def test_pending_qv_passes_only_production_queries(self):
        adapter = MainQVAdapter(connect=lambda: None)
        with patch("intelligence.search_planner.query_validation.service.pending_validated_outbox",
                   return_value=[{"outbox_id": R}]) as fn:
            got = adapter.pending_validated_outbox(P, limit=20)
        self.assertEqual(got, [{"outbox_id": R}])
        fn.assert_called_once_with(P, limit=20, include_test=False, connect=adapter._connect)

    def test_bundle_has_no_test_fixture_override(self):
        adapter = MainQVAdapter(connect=lambda: None)
        with patch("intelligence.search_planner.query_validation.service.get_validated_bundle",
                   return_value={"schema_version": "validated_queries/v1"}) as fn:
            result = adapter.get_validated_bundle(P, R)
        self.assertEqual(result["schema_version"], "validated_queries/v1")
        fn.assert_called_once_with(P, R, include_test=False, connect=adapter._connect)

    def test_bad_scopes_never_call_source(self):
        adapter = MainQVAdapter()
        with patch("intelligence.search_planner.query_validation.service.pending_validated_outbox") as fn:
            with self.assertRaises(CollectorError):
                adapter.pending_validated_outbox("not_project_id")
            with self.assertRaises(CollectorError):
                adapter.pending_validated_outbox(P, limit=True)
            fn.assert_not_called()
        with patch("intelligence.search_planner.query_validation.service.get_validated_bundle") as fn:
            with self.assertRaises(CollectorError):
                adapter.get_validated_bundle(P, "123")
            fn.assert_not_called()


class DTKPortTests(TestCase):
    def test_provider_is_wrapper_not_another_transport(self):
        source = DTKSourceProvider()
        with patch("intelligence.connectors.dtk.status", return_value={"available": False}) as state:
            self.assertEqual(source.status(), {"available": False})
            state.assert_called_once()
        with patch("intelligence.connectors.dtk.submit", return_value={"task_id": "abc"}) as submit:
            self.assertEqual(source.submit("search_posts", {"keyword": "fitness"})["task_id"], "abc")
            submit.assert_called_once_with("search_posts", {"keyword": "fitness"})
        with patch("intelligence.connectors.dtk.task", return_value={"state": "done"}) as task:
            self.assertEqual(source.task("abc")["state"], "done")
            task.assert_called_once_with("abc")

    def test_rejects_unsupported_and_invalid_without_network(self):
        source = DTKSourceProvider()
        with patch("intelligence.connectors.dtk.submit") as submit:
            for action, params in [
                ("hashtag_posts", {"hashtag_id": "123"}),
                ("search_posts", {"keyword": "fitness", "cursor": "none"}),
                ("search_posts", "fitness"),
            ]:
                with self.subTest(action=action, params=params):
                    with self.assertRaises(CollectorError):
                        source.submit(action, params)
            submit.assert_not_called()
        with patch("intelligence.connectors.dtk.task") as task:
            with self.assertRaises(CollectorError):
                source.task(123)
            task.assert_not_called()
