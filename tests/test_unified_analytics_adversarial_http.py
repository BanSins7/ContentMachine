"""UA31 adversarial transport, JSON and project isolation regression.

No network, no external providers: invokes actual local UA26 HTTP adapter
with fake handler and injected UAE service/DB. Every malformed request must
return an explicit bounded error before writes and never leak tokens/DSNs.
"""
from __future__ import annotations
from contextlib import contextmanager
from io import BytesIO
from urllib.parse import quote,urlparse
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import http_api as api
from intelligence.unified_analytics import http_service as svc
from test_unified_analytics_http_api import (
    PROJECT,OTHER,TOKEN,TOKEN_2,REQUEST,JOB,HASHES,
    HandlerFake,FakeHTTPDB,request_body,QueueMock,
)


class AdversarialHTTP(unittest.TestCase):
    def setUp(self):
        self.db=FakeHTTPDB()
        self.path=f"/api/projects/{PROJECT}/unified-analytics"
    @contextmanager
    def service(self):
        with patch.object(api,"local_http_service",
                          lambda:svc.UAEHTTPService(lambda:self.db,HASHES)):
            yield
    def post(self,body=b"{}",route="/qualify",headers=None):
        h=HandlerFake("POST",body,**(headers or {}))
        with self.service():
            self.assertTrue(api.handle_post(h,self.path+route))
        self.assertEqual(len(h.responses),1)
        return h.responses[0]
    def get(self,path="/snapshots",qs="",headers=None):
        h=HandlerFake("GET",**(headers or {}))
        with self.service():
            self.assertTrue(api.handle_get(h,urlparse(self.path+path+qs)))
        self.assertEqual(len(h.responses),1)
        return h.responses[0]
    def assert_no_write(self):
        self.assertEqual(self.db.writes,[])

    def test_duplicate_top_level_json_rejected(self):
        status,out=self.post(b'{"request_id":"x","request_id":"y"}')
        self.assertEqual((status,out["code"]),(400,"duplicate_json_key"))
        self.assert_no_write()

    def test_duplicate_nested_json_rejected(self):
        status,out=self.post(b'{"metadata":{"x":1,"x":2}}')
        self.assertEqual((status,out["code"]),(400,"duplicate_json_key"))
        self.assert_no_write()

    def test_nonfinite_nan_json_rejected(self):
        status,out=self.post(b'{"sample":NaN}')
        self.assertEqual((status,out["code"]),(400,"nonfinite_json"))
        self.assert_no_write()

    def test_nonfinite_infinity_json_rejected(self):
        status,out=self.post(b'{"sample":Infinity}')
        self.assertEqual((status,out["code"]),(400,"nonfinite_json"))
        self.assert_no_write()

    def test_nonfinite_minus_infinity_json_rejected(self):
        status,out=self.post(b'{"sample":-Infinity}')
        self.assertEqual((status,out["code"]),(400,"nonfinite_json"))
        self.assert_no_write()

    def test_invalid_utf8_fails_closed(self):
        status,out=self.post(b'{"x":"\xff"}')
        self.assertEqual((status,out["code"]),(400,"invalid_json"))
        self.assert_no_write()

    def test_truncated_json_fails_closed(self):
        status,out=self.post(b'{"x":')
        self.assertEqual((status,out["code"]),(400,"invalid_json"))
        self.assert_no_write()

    def test_null_json_object_disallowed(self):
        status,out=self.post(b'null')
        self.assertEqual((status,out["code"]),(400,"invalid_body"))
        self.assert_no_write()

    def test_array_json_disallowed(self):
        status,out=self.post(b'[1,2,3]')
        self.assertEqual((status,out["code"]),(400,"invalid_body"))
        self.assert_no_write()

    def test_empty_body_rejected_without_db(self):
        status,out=self.post(b"")
        self.assertEqual(status,413)
        self.assert_no_write()

    def test_body_above_40kb_rejected_without_db(self):
        status,out=self.post(b'{"x":"'+b"a"*40000+b'"}')
        self.assertEqual(status,413)
        self.assert_no_write()

    def test_short_content_length_rejected_without_db(self):
        status,out=self.post(b'{"x":1}',headers={"Content-Length":"100"})
        self.assertEqual((status,out["code"]),(400,"invalid_body_size"))
        self.assert_no_write()

    def test_negative_content_length_rejected(self):
        status,out=self.post(b'{"x":1}',headers={"Content-Length":"-1"})
        self.assertEqual(status,413)
        self.assert_no_write()

    def test_exponentially_nested_json_rejected(self):
        body=(b'{"x":'*1500)+b'0'+(b"}"*1500)
        status,_=self.post(body)
        self.assertEqual(status,400)
        self.assert_no_write()

    def test_invalid_content_type_rejected(self):
        status,out=self.post(b'{}',headers={"Content-Type":"text/plain"})
        self.assertEqual(status,415)
        self.assert_no_write()

    def test_unknown_json_field_does_not_enqueue(self):
        status,out=self.post(b'{"__proto__":{"admin":true}}')
        self.assertEqual(status,400)
        self.assert_no_write()

    def test_sql_injection_in_page_limit_rejected_pre_db(self):
        status,out=self.get(qs="?limit="+quote("1 OR 1=1"))
        self.assertEqual((status,out["code"]),(400,"invalid_query"))
        self.assertEqual(self.db.queries,[])

    def test_sql_injection_in_before_rejected_pre_db(self):
        status,out=self.get(qs="?before="+quote("0;DROP TABLE videos"))
        self.assertEqual((status,out["code"]),(400,"invalid_query"))
        self.assertEqual(self.db.queries,[])

    def test_query_duplicate_key_rejected(self):
        status,out=self.get(qs="?limit=3&limit=4")
        self.assertEqual((status,out["code"]),(400,"invalid_query"))
        self.assertEqual(self.db.queries,[])

    def test_query_unknown_field_rejected(self):
        status,out=self.get(qs="?state=qualified")
        self.assertEqual((status,out["code"]),(400,"invalid_query"))
        self.assertEqual(self.db.queries,[])

    def test_query_too_many_fields_rejected(self):
        status,out=self.get(qs="?"+"&".join("v"+str(i)+"=1" for i in range(9)))
        self.assertEqual((status,out["code"]),(400,"invalid_query"))
        self.assertEqual(self.db.queries,[])

    def test_numeric_boollike_limit_rejected(self):
        status,out=self.get(qs="?limit=true")
        self.assertEqual(status,400)
        self.assertEqual(self.db.queries,[])

    def test_numeric_overflow_limit_rejected(self):
        status,out=self.get(qs="?limit="+str(2**100))
        self.assertEqual(status,400)
        self.assertEqual(self.db.queries,[])

    def test_snapshot_sql_injection_route_has_no_match(self):
        status,out=self.get(path="/snapshots/"+quote("1 UNION SELECT * FROM videos"))
        self.assertEqual(status,404)
        self.assert_no_write()

    def test_wrong_project_token_rejected_before_db(self):
        status,out=self.get(headers={"X-UAE-Project-Token":TOKEN_2})
        self.assertEqual((status,out["code"]),(403,"project_forbidden"))
        self.assertEqual(self.db.queries,[])

    def test_cross_project_scope_cannot_reuse_rights_token(self):
        h=HandlerFake()
        with self.service():
            api.handle_get(h,urlparse(
                f"/api/projects/{OTHER}/unified-analytics/snapshots"))
        self.assertEqual(h.responses[0][0],403)
        self.assertEqual(self.db.queries,[])

    def test_unsupported_http_version_rejected_pre_db(self):
        status,out=self.get(headers={"X-UAE-Api-Version":"uae-local-http/v2"})
        self.assertEqual((status,out["code"]),(409,"unsupported_api_version"))
        self.assertEqual(self.db.queries,[])

    def test_csrf_mutation_denied_before_body_or_db(self):
        h=HandlerFake("POST",b'{"x":1}')
        h.mutation_allowed=False
        with self.service():
            api.handle_post(h,self.path+"/qualify")
        self.assertEqual(h.responses[0][0],403)
        self.assertEqual(self.db.queries,[])

    def test_host_origin_get_denied_pre_db(self):
        h=HandlerFake("GET")
        h.local=False
        with self.service():
            api.handle_get(h,urlparse(self.path+"/snapshots"))
        self.assertEqual(h.responses[0][0],403)
        self.assertEqual(self.db.queries,[])

    def test_aggregate_confidential_gate_remains_409(self):
        status,out=self.get(path="/aggregates")
        self.assertEqual((status,out["code"]),
                         (409,"aggregate_requires_live_qualified_pins"))
        self.assert_no_write()

    def test_qualified_snapshot_detail_not_disclosed(self):
        status,out=self.get(path="/snapshots/7")
        self.assertEqual(status,404)
        self.assert_no_write()

    def test_no_token_or_dsns_reflected_in_error_json(self):
        status,out=self.post(b'{"x":"'+TOKEN.encode()+b'"}')
        text=str(out)
        self.assertEqual(status,400)
        self.assertNotIn(TOKEN,text)
        self.assertNotIn("postgresql://",text)
        self.assertNotIn("Traceback",text)


if __name__=="__main__":
    unittest.main()
