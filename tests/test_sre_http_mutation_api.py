"""M39 localhost CSRF/same-origin/strict-JSON and independent auth tests."""
import io
import json
import unittest
from unittest.mock import patch

from intelligence.relevance.http_mutation_api import (
    LocalSREMutationAPI,MAX_JSON_BYTES,
)
from intelligence.relevance.http_read_api import LocalSREReadAPI,ROOT
from intelligence.relevance.mutation_service import TrustedSREMutationService

PID="a"*32
OTHER="b"*32
RUN="11111111-1111-4111-8111-111111111111"
KEY="mutation-20261028-01"
ACTOR="trusted:human"


class MutationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.csrf=[]
        self.service=TrustedSREMutationService(
            connect=lambda:None,
            actor_authorizer=lambda pid,actor,action:pid==PID and actor==ACTOR,
        )
        self.app=LocalSREMutationAPI(
            service=self.service,
            actor_resolver=lambda env:env.get("HOST_SESSION_ACTOR"),
            csrf_verifier=lambda env,actor,origin,token:
                self.csrf.append((actor,origin,token)) is None
                and token=="session-bound-verified-token",
            rate_limiter=lambda pid,actor,action:True,
            enabled=True,
        )
        self.disabled=LocalSREMutationAPI(
            service=self.service,
            actor_resolver=lambda env:env.get("HOST_SESSION_ACTOR"),
            csrf_verifier=lambda *a:True,
            rate_limiter=lambda *a:True,
        )
    def invoke(self,*,path=None,method="POST",body=None,headers=None,
               actor=ACTOR,origin="http://127.0.0.1:8765",raw=None):
        path=path or ROOT+"/projects/"+PID+"/mutations/cancel_run"
        raw=(json.dumps(body if body is not None else {"run_id":RUN},
                        separators=(",",":")).encode("utf-8") if raw is None else raw)
        env={
            "REMOTE_ADDR":"127.0.0.1","HTTP_HOST":"127.0.0.1:8765",
            "REQUEST_METHOD":method,"PATH_INFO":path,"QUERY_STRING":"",
            "CONTENT_TYPE":"application/json","CONTENT_LENGTH":str(len(raw)),
            "wsgi.input":io.BytesIO(raw),
            "HTTP_ORIGIN":origin,
            "HTTP_X_CSRF_TOKEN":"session-bound-verified-token",
            "HTTP_IDEMPOTENCY_KEY":KEY,
            "HOST_SESSION_ACTOR":actor,
            "HTTP_SEC_FETCH_SITE":"same-origin",
        }
        if headers:env.update(headers)
        status=[]
        body=b"".join(self.app(env,lambda s,h:status.append((s,dict(h)))))
        return status[0][0],status[0][1],json.loads(body)
    def test_verified_origin_csrf_and_body_forwarded_to_trusted_service(self):
        with patch.object(self.service,"perform",return_value={
            "state":"committed","replayed":False,"paid_calls":0}) as call:
            status,headers,body=self.invoke()
        self.assertEqual(status,"200 OK")
        self.assertEqual(body["paid_calls"],0)
        self.assertEqual(headers["Cache-Control"],"no-store, max-age=0")
        call.assert_called_once_with(
            PID,ACTOR,"cancel_run",KEY,{"run_id":RUN})
        self.assertEqual(self.csrf[0],(
            ACTOR,"http://127.0.0.1:8765","session-bound-verified-token"))
    def test_host_actor_cannot_be_sourced_from_http_headers(self):
        with patch.object(self.service,"perform") as call:
            self.assertEqual(self.invoke(actor=None,headers={
                "HTTP_X_ACTOR_ID":ACTOR,"HTTP_X_PROJECT_ID":PID})[0],
                "403 Forbidden")
            call.assert_not_called()
    def test_disabled_by_default_no_db_or_cookies(self):
        status=[]
        response=b"".join(self.disabled({
            "REQUEST_METHOD":"POST","REMOTE_ADDR":"127.0.0.1",
        },lambda s,h:status.append(s)))
        self.assertEqual(status,["503 Service Unavailable"])
        self.assertIn(b"sre_mutations_disabled",response)
    def test_mutation_does_not_accept_different_origin_or_remote(self):
        cases=[
            {"HTTP_ORIGIN":"https://127.0.0.1:8765"},
            {"HTTP_ORIGIN":"http://evil.example"},
            {"HTTP_ORIGIN":"null"},
            {"HTTP_ORIGIN":""},
            {"HTTP_SEC_FETCH_SITE":"cross-site"},
            {"HTTP_SEC_FETCH_SITE":"none"},
            {"HTTP_X_FORWARDED_FOR":"127.0.0.1"},
            {"REMOTE_ADDR":"172.16.0.1"},
            {"HTTP_HOST":"attacker.net"},
            {"HTTP_X_CSRF_TOKEN":"bad"},
            {"HTTP_X_CSRF_TOKEN":""},
        ]
        with patch.object(self.service,"perform") as call:
            for headers in cases:
                with self.subTest(headers=headers):
                    status,_,_=self.invoke(headers=headers)
                    self.assertEqual(status,"403 Forbidden")
            call.assert_not_called()
    def test_shared_host_quota_denies_flood_before_database(self):
        denied=LocalSREMutationAPI(
            service=self.service,
            actor_resolver=lambda env:env.get("HOST_SESSION_ACTOR"),
            csrf_verifier=lambda *x:True,
            rate_limiter=lambda pid,actor,action:False,
            enabled=True)
        prior=self.app
        self.app=denied
        try:
            with patch.object(self.service,"perform") as perform:
                status,_,payload=self.invoke()
                self.assertEqual(status,"429 Too Many Requests")
                self.assertEqual(payload,{"error":"rate_limited"})
                perform.assert_not_called()
        finally:
            self.app=prior

    def test_json_bomb_duplicate_keys_invalid_content_type_and_overflow(self):
        cases=[
            {"CONTENT_TYPE":"text/plain"},
            {"CONTENT_LENGTH":"0"},
            {"CONTENT_LENGTH":str(MAX_JSON_BYTES+1)},
            {"CONTENT_LENGTH":"999999999"},
            {"QUERY_STRING":"force=true"},
        ]
        with patch.object(self.service,"perform") as call:
            for headers in cases:
                with self.subTest(headers=headers):
                    self.assertEqual(self.invoke(headers=headers)[0],
                                     "400 Bad Request")
            for raw in (
                b'{"run_id":"x","run_id":"y"}',
                b'{"run_id":NaN}',
                b'["not-object"]',
                b'\xff\xfe',
                b'{} trailing',
            ):
                with self.subTest(raw=raw):
                    self.assertEqual(self.invoke(raw=raw)[0],
                                     "400 Bad Request")
            call.assert_not_called()
    def test_only_post_known_mutation_and_canonical_project(self):
        with patch.object(self.service,"perform") as call:
            self.assertEqual(self.invoke(method="DELETE")[0],
                             "405 Method Not Allowed")
            self.assertEqual(self.invoke(method="PUT")[0],
                             "405 Method Not Allowed")
            self.assertEqual(self.invoke(path=ROOT+"/projects/"+PID+
                                         "/mutations/dispatch_paid")[0],
                             "404 Not Found")
            self.assertEqual(self.invoke(path=ROOT+"/projects/"+OTHER.upper()+
                                         "/mutations/cancel_run")[0],
                             "404 Not Found")
            call.assert_not_called()
    def test_service_exception_redacted_and_in_doubt_receipt_is_conflict(self):
        from intelligence.relevance.v1_contracts import SREContractError
        with patch.object(self.service,"perform",
                          side_effect=RuntimeError("postgres://password:key@db")):
            status,_,body=self.invoke()
            self.assertEqual(status,"503 Service Unavailable")
            self.assertNotIn("postgres",str(body))
        with patch.object(self.service,"perform",side_effect=SREContractError(
            "mutation_in_doubt","key","unknown charge")):
            status,_,body=self.invoke()
            self.assertEqual(status,"409 Conflict")
            self.assertEqual(body,{"error":"mutation_in_doubt"})
    def test_get_never_routes_to_mutation_service(self):
        read=LocalSREReadAPI(
            connect=lambda:None,
            actor_resolver=lambda env:ACTOR,
            actor_authorizer=lambda *x:True,
            enabled=True)
        composite=LocalSREMutationAPI(
            service=self.service,actor_resolver=lambda env:ACTOR,
            csrf_verifier=lambda *x:True,
            rate_limiter=lambda *x:True,read_api=read,enabled=True)
        replies=[]
        output=b"".join(composite({
            "REMOTE_ADDR":"127.0.0.1","HTTP_HOST":"localhost:8765",
            "REQUEST_METHOD":"GET","PATH_INFO":ROOT+"/manifest",
            "QUERY_STRING":"",
        },lambda s,h:replies.append(s)))
        self.assertEqual(replies,["200 OK"])
        self.assertIn(b"read_only",output)


if __name__=="__main__":
    unittest.main()
