"""RTB FIX-09-A local HTTP boundary security tests, no DB and no Opus."""
from __future__ import annotations

import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
from urllib.parse import urlparse

from intelligence.taxonomy_builder import deep_v2_http as http
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID="a"*32
RUN="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"
BASE=f"/api/projects/{PID}/taxonomy-builder/deep-v2"


class FakeHandler:
    def __init__(self,data=None,*,origin="http://127.0.0.1:8765"):
        raw=json.dumps(data if data is not None else {}).encode("utf8")
        self.rfile=io.BytesIO(raw)
        self.headers={
            "Origin":origin,
            "Content-Type":"application/json",
            "Content-Length":str(len(raw)),
        }
        self.responses=[]

    def json_response(self,status,body):
        self.responses.append((status,body))


class DeepV2HTTPTest(unittest.TestCase):
    def test_server_actor_from_env_only_and_missing_disables_routes(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(PipelineError,"not_configured"):
                http._actor()
        with patch.dict(os.environ,{"CM_RTB_REVIEWER_ID":"allowed-human"}):
            self.assertEqual(http._actor(),"allowed-human")
        with patch.dict(os.environ,{"CM_RTB_REVIEWER_ID":" forged " }):
            with self.assertRaises(PipelineError):
                http._actor()

    def test_no_body_review_or_provider_impersonation(self):
        for data in (
            {"reviewer_id":"admin"},
            {"actor":"owner"},
            {"api_key":"sk-or-bad"},
            {"sender":"test"},
            {"provider_calls":0},
        ):
            with self.subTest(payload=data):
                with self.assertRaisesRegex(PipelineError,"forbidden"):
                    http._body(FakeHandler(data))

    def test_get_only_matches_new_module_namespace(self):
        h=FakeHandler()
        with patch.dict(os.environ,{"CM_RTB_REVIEWER_ID":"actor"}):
            self.assertFalse(http.handle_get(
                h,urlparse(f"/api/projects/{PID}/taxonomy-builder/runs/{RUN}/deep")))
            self.assertFalse(http.handle_post(
                h,f"/api/projects/{PID}/taxonomy-builder/runs/{RUN}/deep/start"))
        self.assertEqual(h.responses,[])

    def test_paid_origin_must_be_exact_browser_origin(self):
        action=f"runs/{RUN}/advance-pass1"
        for origin in ("","null","http://evil.example",
                       "http://127.0.0.1:8765.evil.com",
                       "https://127.0.0.1:8765"):
            with self.subTest(origin=origin), self.assertRaisesRegex(
                PipelineError,"same_origin_required"):
                http.dispatch_post(PID,action,{"authorize_paid_call":True},
                                   actor="approved",origin=origin)

    def test_paid_true_flag_cannot_be_string_or_missing(self):
        action=f"runs/{RUN}/advance-pass2"
        for body in ({"authorize_paid_call":"true"},
                     {"authorize_paid_call":1},{}):
            with self.subTest(body=body), self.assertRaisesRegex(
                PipelineError,"explicit_action_required"):
                http.dispatch_post(PID,action,body,actor="reviewer",
                                   origin="http://127.0.0.1:8765")

    def test_explicit_paid_action_invokes_only_selected_step(self):
        actor="trusted-server-actor"
        obj=Mock()
        obj.advance_pass1.return_value={"status":"review1","provider_calls":1}
        with patch.object(http,"_repo",return_value=obj) as factory:
            result=http.dispatch_post(
                PID,f"runs/{RUN}/advance-pass1",
                {"authorize_paid_call":True},actor,
                origin="http://localhost:8765")
            self.assertEqual(result["status"],"review1")
            obj.advance_pass1.assert_called_once_with(PID,RUN,actor)
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[:2],
                             ("deep_v2_pass1","RTBDeepV2Pass1Repository"))
        # Wrong optional parameter cannot pass to paid repository.
        with self.assertRaisesRegex(PipelineError,"invalid_fields"):
            http.dispatch_post(PID,f"runs/{RUN}/advance-pass1",
                {"authorize_paid_call":True,"sender":"malicious"},
                actor,origin="http://localhost:8765")

    def test_get_status_never_calls_paid_advance(self):
        obj=Mock()
        obj.inspect_pass1.return_value={"status":"pass1_pending"}
        with patch.object(http,"_repo",return_value=obj):
            response=http.dispatch_get(PID,f"runs/{RUN}/status","server-reviewer")
        self.assertEqual(response["status"],"pass1_pending")
        obj.inspect_pass1.assert_called_once_with(PID,RUN,"server-reviewer")
        obj.advance_pass1.assert_not_called()

    def test_publication_get_and_v1_v2_export_require_explicit_version(self):
        obj=Mock()
        obj.export.return_value={"schema_version":"rtb-publication-export/v2"}
        for version in ("rtb-publication-export/v1","rtb-publication-export/v2"):
            with patch.object(http,"_repo",return_value=obj):
                result=http.dispatch_get(
                    PID,f"exports/{PUB}","server-reviewer",
                    "version="+version)
                self.assertEqual(result["schema_version"],
                                 "rtb-publication-export/v2")
        for query in ("", "version=bad", "version=x&version=y", "foo=bar"):
            with self.subTest(query=query), self.assertRaises(PipelineError):
                http.dispatch_get(PID,f"exports/{PUB}","actor",query)

    def test_review_and_publication_do_not_allow_foreign_payload_fields(self):
        for action,data in (
            ("review1",{"decisions":[],"reviewer_id":"hacker"}),
            ("review2",{"decisions":[],"run_id":RUN}),
            ("publish",{"confirm_publication":"yes"}),
        ):
            with self.subTest(action=action),self.assertRaises(PipelineError):
                http.dispatch_post(PID,f"runs/{RUN}/{action}",data,
                                   "trusted","http://localhost:8765")

    def test_expansion_consent_is_separate_from_original_base_consent(self):
        obj=Mock()
        obj.authorize_expansion.return_value={"consent_recorded":True}
        with patch.object(http,"_repo",return_value=obj):
            result=http.dispatch_post(
                PID,f"runs/{RUN}/expansion-consent",
                {"quote_id":PUB,"authorized_usd":"0.055",
                 "acknowledged_quote_sha256":"b"*64},
                "server-reviewer",origin="")
        self.assertTrue(result["consent_recorded"])
        obj.authorize_expansion.assert_called_once_with(
            PID,PUB,"server-reviewer","0.055",
            acknowledged_quote_sha256="b"*64)
        obj.advance_expansion.assert_not_called()

    def test_no_configured_actor_returns_503_before_any_dispatch(self):
        handler=FakeHandler()
        with patch.dict(os.environ,{},clear=True):
            handled=http.handle_get(handler,urlparse(BASE+f"/runs/{RUN}/status"))
        self.assertTrue(handled)
        self.assertEqual(handler.responses[0][0],503)
        self.assertNotIn("reviewer_id",handler.responses[0][1])

    def test_oversized_wrong_media_and_non_object_json_fail_closed(self):
        handler=FakeHandler()
        handler.headers["Content-Type"]="text/plain"
        with self.assertRaisesRegex(PipelineError,"json_required"):
            http._body(handler)
        handler=FakeHandler()
        handler.headers["Content-Length"]="999999999"
        with self.assertRaisesRegex(PipelineError,"too_large"):
            http._body(handler)
        handler=FakeHandler()
        handler.rfile=io.BytesIO(b'[]')
        with self.assertRaisesRegex(PipelineError,"object_required"):
            http._body(handler)

    def test_no_paid_post_on_cross_project_or_malformed_id(self):
        for bad in ("f"*31,"http://evil.example","BAD"):
            with self.subTest(project=bad),self.assertRaises(PipelineError):
                http.dispatch_post(
                    bad,f"runs/{RUN}/advance-pass1",
                    {"authorize_paid_call":True},"reviewer",
                    origin="http://127.0.0.1:8765")

if __name__=="__main__":
    unittest.main()
