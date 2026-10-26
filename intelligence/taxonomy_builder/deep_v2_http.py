"""RTB FIX-09-A: opt-in loopback Deep V2 routes with server-bound B9 reviewer.

Legacy dashboard is local single-operator, not multiuser authentication.
CM_RTB_REVIEWER_ID is a trusted server configuration, NEVER a client value.
SQL independently checks that identity in the requested project.
GET never pays; every paid POST requires explicit intent and real Origin.
"""
from __future__ import annotations
import json
import os
import re
from urllib.parse import parse_qs
from .pipeline_contracts import PipelineError

PREFIX=re.compile(r"^/api/projects/([0-9a-f]{32})/taxonomy-builder/deep-v2(?:/(.*))?$")
UUID=r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ORIGINS=frozenset(("http://127.0.0.1:8765","http://localhost:8765"))
PAID=frozenset(("advance-pass1","advance-pass2","advance-expansion"))
MAX_BYTES=150000

def _fail(code,status=422):
    raise PipelineError(code,status)

def _actor():
    who=os.environ.get("CM_RTB_REVIEWER_ID","")
    if not who or len(who)>160 or who!=who.strip() or any(ord(c)<32 for c in who):
        _fail("rtb_v2_http_server_reviewer_not_configured",503)
    return who

def _body(handler):
    if handler.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
        _fail("rtb_v2_http_json_required",415)
    try:
        n=int(handler.headers.get("Content-Length","0"))
    except (ValueError,TypeError):
        _fail("rtb_v2_http_bad_length",413)
    if not 2<=n<=MAX_BYTES:
        _fail("rtb_v2_http_body_too_large",413)
    try:
        payload=json.loads(handler.rfile.read(n).decode("utf-8"))
    except (ValueError,UnicodeError,TypeError):
        _fail("rtb_v2_http_invalid_json")
    if not isinstance(payload,dict):
        _fail("rtb_v2_http_object_required")
    if any(x in payload for x in ("reviewer_id","actor","api_key","sender","provider_calls")):
        _fail("rtb_v2_http_browser_identity_or_provider_forbidden",403)
    return payload

def _fields(body,required=(),optional=()):
    if set(body)-set(required)-set(optional) or set(required)-set(body):
        _fail("rtb_v2_http_invalid_fields")

def _repo(module,cls,factory):
    from importlib import import_module
    klass=getattr(import_module("intelligence.taxonomy_builder."+module),cls)
    return klass(**({"connection_factory":factory} if factory is not None else {}))

def dispatch_get(pid,route,actor,query="",factory=None):
    from .research_contracts_v2 import _project
    _project(pid)
    if not route:
        _fail("rtb_v2_http_route_not_found",404)
    if re.fullmatch(r"bindings/("+UUID+r")",route):
        if query: _fail("rtb_v2_http_query_disallowed")
        return _repo("deep_v2_binding_repository","RTBDeepV2BindingRepository",factory).get(pid,route[9:],actor)
    m=re.fullmatch(r"runs/("+UUID+r")/(status|pass2-status|review1|review2-preview|review2|publication)",route)
    if m:
        if query: _fail("rtb_v2_http_query_disallowed")
        rid,action=m.groups()
        if action=="status":
            return _repo("deep_v2_pass1","RTBDeepV2Pass1Repository",factory).inspect_pass1(pid,rid,actor)
        if action=="pass2-status":
            return _repo("deep_v2_pass2","RTBDeepV2Pass2Repository",factory).inspect_pass2(pid,rid,actor)
        if action=="review1":
            return _repo("deep_v2_review_repository","RTBDeepV2HumanReviewRepository",factory).get_review1(pid,rid,actor)
        if action=="review2-preview":
            return _repo("deep_v2_review2_repository","RTBDeepV2Review2Repository",factory).preview_review2(pid,rid,actor)
        if action=="review2":
            return _repo("deep_v2_review2_repository","RTBDeepV2Review2Repository",factory).get_review2(pid,rid,actor)
        return _repo("deep_v2_publication","RTBDeepV2PublicationRepository",factory).get_publication(pid,rid,actor)
    m=re.fullmatch(r"exports/("+UUID+r")",route)
    if m:
        try: params=parse_qs(query,strict_parsing=True,keep_blank_values=True)
        except ValueError: _fail("rtb_v2_http_invalid_export_query")
        if set(params)!={"version"} or len(params["version"])!=1 or params["version"][0] not in (
            "rtb-publication-export/v1","rtb-publication-export/v2"):
            _fail("rtb_v2_http_explicit_version_required")
        return _repo("publication_export_v2","RTBDeepV2PublicationExportRepository",factory).export(
            pid,m[1],actor,accepted_versions=params["version"])
    _fail("rtb_v2_http_route_not_found",404)

def dispatch_post(pid,route,data,actor,origin="",factory=None):
    from .research_contracts_v2 import _project
    _project(pid)
    # The browser's same-origin POST Origin is mandatory for every Deep V2
    # mutation, including grants and budget authorizations.
    if origin not in ORIGINS:
        _fail("rtb_v2_http_post_same_origin_required",403)
    if route=="bindings/pin":
        _fields(data,("snapshot_id",))
        return _repo("deep_v2_binding_repository","RTBDeepV2BindingRepository",factory).pin(pid,data["snapshot_id"],actor)
    if route=="quotes":
        _fields(data,("binding_id",),("max_output_tokens",))
        kwargs={"max_output_tokens":data["max_output_tokens"]} if "max_output_tokens" in data else {}
        return _repo("deep_v2_quote_repository","RTBDeepV2QuoteRepository",factory).quote(pid,data["binding_id"],actor,**kwargs)
    if route in ("consents","consents/revoke","runs/start"):
        quote=_repo("deep_v2_quote_repository","RTBDeepV2QuoteRepository",factory)
    if route=="consents":
        _fields(data,("quote_id","authorized_usd","acknowledged_quote_sha256"))
        return quote.authorize(pid,data["quote_id"],actor,data["authorized_usd"],
                               acknowledged_quote_sha256=data["acknowledged_quote_sha256"])
    if route=="consents/revoke":
        _fields(data,("consent_id",))
        return quote.revoke(pid,data["consent_id"],actor)
    if route=="runs/start":
        _fields(data,("quote_id","consent_id","idempotency_key"))
        return quote.start(pid,data["quote_id"],data["consent_id"],actor,data["idempotency_key"])
    m=re.fullmatch(r"runs/("+UUID+r")/(advance-pass1|review1|advance-pass2|review2|publish|expansion-quote|expansion-consent|expansion-revoke|advance-expansion)",route or "")
    if not m:
        _fail("rtb_v2_http_route_not_found",404)
    rid,action=m.groups()
    if action in PAID:
        if origin not in ORIGINS:
            _fail("rtb_v2_http_paid_same_origin_required",403)
        if data.get("authorize_paid_call") is not True:
            _fail("rtb_v2_http_paid_explicit_action_required",403)
    if action=="advance-pass1":
        _fields(data,("authorize_paid_call",))
        return _repo("deep_v2_pass1","RTBDeepV2Pass1Repository",factory).advance_pass1(pid,rid,actor)
    if action=="advance-pass2":
        _fields(data,("authorize_paid_call",))
        return _repo("deep_v2_pass2","RTBDeepV2Pass2Repository",factory).advance_pass2(pid,rid,actor)
    if action=="review1":
        _fields(data,("decisions",))
        return _repo("deep_v2_review_repository","RTBDeepV2HumanReviewRepository",factory).submit_review1(pid,rid,actor,data["decisions"])
    if action=="review2":
        _fields(data,("decisions",))
        return _repo("deep_v2_review2_repository","RTBDeepV2Review2Repository",factory).submit_review2(pid,rid,actor,data["decisions"])
    if action=="publish":
        _fields(data,("confirm_publication",))
        if data["confirm_publication"] is not True:
            _fail("rtb_v2_http_publish_confirmation_required",403)
        return _repo("deep_v2_publication","RTBDeepV2PublicationRepository",factory).publish(pid,rid,actor)
    expansion=_repo("deep_v2_expansion","RTBDeepV2ExpansionRepository",factory)
    if action=="expansion-quote":
        _fields(data,optional=("max_output_tokens",))
        kwargs={"max_output_tokens":data["max_output_tokens"]} if "max_output_tokens" in data else {}
        return expansion.quote_expansion(pid,rid,actor,**kwargs)
    if action=="expansion-consent":
        _fields(data,("quote_id","authorized_usd","acknowledged_quote_sha256"))
        return expansion.authorize_expansion(pid,data["quote_id"],actor,data["authorized_usd"],
                                            acknowledged_quote_sha256=data["acknowledged_quote_sha256"])
    if action=="expansion-revoke":
        _fields(data,("consent_id",))
        return expansion.revoke_expansion(pid,data["consent_id"],actor)
    _fields(data,("consent_id","authorize_paid_call"))
    return expansion.advance_expansion(pid,rid,data["consent_id"],actor)

def handle_get(handler,url):
    m=PREFIX.fullmatch(url.path)
    if not m:return False
    try:
        response=dispatch_get(m[1],m[2],_actor(),url.query)
        handler.json_response(200,{"ok":True,**response})
    except PipelineError as exc:
        handler.json_response(exc.http_status,{"ok":False,"error":str(exc)})
    except Exception as exc:
        from intelligence.knowledge_extraction.intake import IntakeError
        if isinstance(exc,IntakeError):
            handler.json_response(
                getattr(exc,"http_status",403),
                {"ok":False,"error":"rtb_v2_http_b9_reviewer_denied"})
        else:
            raise
    return True

def handle_post(handler,path):
    m=PREFIX.fullmatch(path)
    if not m:return False
    try:
        response=dispatch_post(m[1],m[2],_body(handler),_actor(),
                               handler.headers.get("Origin",""))
        handler.json_response(200,{"ok":True,**response})
    except PipelineError as exc:
        handler.json_response(exc.http_status,{"ok":False,"error":str(exc)})
    except Exception as exc:
        from intelligence.knowledge_extraction.intake import IntakeError
        if isinstance(exc,IntakeError):
            handler.json_response(
                getattr(exc,"http_status",403),
                {"ok":False,"error":"rtb_v2_http_b9_reviewer_denied"})
        else:
            raise
    return True
