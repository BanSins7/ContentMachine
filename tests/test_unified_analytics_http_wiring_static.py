"""UA26 app hookup, route isolation and token security static tests."""
from __future__ import annotations

from pathlib import Path
import unittest

from intelligence.unified_analytics import http_api, http_service


class AppRouteWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=(Path(__file__).resolve().parents[1]/"app.py").read_text(
            encoding="utf-8",
        )
        cls.service=(Path(__file__).resolve().parents[1]/
                     "intelligence/unified_analytics/http_service.py").read_text(
                         encoding="utf-8",
                     )
        cls.transport=(Path(__file__).resolve().parents[1]/
                       "intelligence/unified_analytics/http_api.py").read_text(
                           encoding="utf-8",
                       )

    def test_get_checks_host_origin_before_uae(self):
        segment=self.app[self.app.index("def do_GET(self):"):
                         self.app.index("def do_POST(self):")]
        self.assertLess(segment.index("if not self.authorized():"),
                        segment.index("uae_get(self,url)"))

    def test_post_checks_host_origin_csrf_before_uae(self):
        segment=self.app[self.app.index("def do_POST(self):"):]
        self.assertLess(segment.index("if not self.authorized(mutation=True):"),
                        segment.index("uae_post(self,route)"))

    def test_only_two_additive_host_hooks(self):
        self.assertEqual(self.app.count("uae_get(self,url)"),1)
        self.assertEqual(self.app.count("uae_post(self,route)"),1)

    def test_http_backend_independent_per_project_token(self):
        self.assertEqual(http_service.PROJECT_TOKENS_ENV,
                         "CM_UAE_HTTP_TOKENS_SHA256")
        self.assertIn('hmac.compare_digest',self.service)
        self.assertIn('hashlib.sha256(bytes.fromhex(token))',self.service)

    def test_no_secret_key_defaults_or_plaintext_auth(self):
        self.assertIn('if len(raw)>20000 or not raw:',self.service)
        self.assertNotIn("DEFAULT_UAE_TOKEN",self.service)
        self.assertNotIn('token == expected',self.service)

    def test_all_selects_are_exact_project_scoped(self):
        for name in (http_service.LIST_SQL,http_service.DETAIL_SQL,
                     http_service.REQUEST_SQL,http_service.JOB_SQL):
            self.assertIn("project_id=%s",name)
        self.assertIn("WHERE id=%s",http_service.PROJECT_SQL)

    def test_malformed_qualified_get_not_published(self):
        self.assertIn("aggregate_requires_live_qualified_pins",self.service)
        self.assertNotIn("WHERE status='qualified'",self.service)
        self.assertNotIn("WHERE status = 'qualified'",self.service)

    def test_no_raw_payload_or_hidden_lease_token_in_json(self):
        fields=self.service.split("def get(self,project_id")[1]
        self.assertIn('"raw_payload_excluded":True',fields)
        self.assertNotIn('("lease_token",',fields)
        self.assertNotIn('"readiness":',fields)
        self.assertNotIn('"blockers":',fields)
        self.assertNotIn('"payload":',fields)

    def test_get_transport_does_not_read_or_mutate_job_queue(self):
        get=self.transport.split("def handle_get(h,url)")[1].split(
            "def handle_post(h,route:str)")[0]
        self.assertNotIn("_json_body(h)",get)
        self.assertNotIn(".post(",get)
        self.assertNotIn(".execute(",get)

    def test_post_transport_has_bounded_json_and_duplicate_key_denial(self):
        self.assertIn("MAX_BODY=40000",self.transport)
        self.assertIn("duplicate_json_key",self.transport)
        self.assertIn("nonfinite_json",self.transport)
        self.assertIn("_access(h,True)",self.transport)

    def test_stable_crossproject_error_and_notfound_codes(self):
        self.assertIn('project_forbidden",403',self.service)
        self.assertIn('project_not_found",404',self.service)
        self.assertIn('job_terminal",409',self.service)
        self.assertIn('aggregate_requires_live_qualified_pins',self.service)

    def test_no_bypass_ua19_or_signed_pie_in_http(self):
        for bad in ("PIEResearchExportReader", "ReceiptSigner",
                    "QualifiedAggregationReader", "status='qualified'",
                    "publish_qualified", "GRANT SELECT",
                    "compute_c_metrics("):
            self.assertNotIn(bad,self.service+self.transport)


if __name__=="__main__":
    unittest.main()
