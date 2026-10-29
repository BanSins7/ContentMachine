"""M39 loopback-only WSGI POST gateway; GET remains delegated to M38.

Host authenticates identity from its OWN session middleware. Browser controlled
project/actor headers do not confer authority. A CSRF callback must validate
the token against the *same verified host session* and the exact Origin.
Origin/Sec-Fetch-Site are defense-in-depth, not authentication by themselves.

This module never starts a server, provider client, paid worker, or DB on import.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .http_read_api import (
    ROOT,PROJECT_RE,LocalSREReadAPI,_response,_safe_request,
)
from .mutation_service import ALLOWED_OPERATIONS,TrustedSREMutationService
from .opus_consent import _actor
from .v1_contracts import SREContractError,project_id,reject

VERSION="sre-localhost-mutation-api/v1"
MAX_JSON_BYTES=32768
MAX_CSRF_LENGTH=256
PATH=re.compile(r"^mutations/([a-z_]{3,32})\Z")
CONTENT_TYPE=re.compile(r"application/json(?:;\s*charset=utf-8)?\Z",re.I)


def _strict_object_pairs(items):
    result={}
    for key,value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key]=value
    return result


def _json_body(environ):
    ct=environ.get("CONTENT_TYPE","")
    if type(ct) is not str or CONTENT_TYPE.fullmatch(ct) is None:
        reject("content_type","JSON application/json required")
    length=environ.get("CONTENT_LENGTH","")
    if (type(length) is not str or
            re.fullmatch(r"[1-9][0-9]{0,4}",length) is None
            or not 0<int(length)<=MAX_JSON_BYTES):
        reject("content_length","nonempty bounded JSON body required")
    stream=environ.get("wsgi.input")
    if stream is None or not callable(getattr(stream,"read",None)):
        reject("body","request body unavailable")
    body=stream.read(int(length))
    if type(body) is not bytes or len(body)!=int(length):
        reject("body","incomplete request body")
    try:
        content=json.loads(body.decode("utf-8"),
                           object_pairs_hook=_strict_object_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               ValueError("non-finite number")))
    except (ValueError,UnicodeError,TypeError):
        reject("body","strict UTF-8 JSON object required")
    if type(content) is not dict:
        reject("body","JSON root must be an object")
    return content


def _same_origin(environ):
    # Host verification already occurs in _safe_request(). Require an exact
    # browser Origin including host+port; never accept null/file/https proxies.
    origin=environ.get("HTTP_ORIGIN")
    host=environ.get("HTTP_HOST")
    if (type(origin) is not str or type(host) is not str or
            origin!="http://"+host):
        reject("origin","exact same-origin localhost browser required",
               "actor_not_authorized")
    try:parts=urlsplit(origin)
    except ValueError:
        reject("origin","invalid origin","actor_not_authorized")
    if parts.scheme!="http" or parts.path or parts.query or parts.fragment:
        reject("origin","not an exact local origin","actor_not_authorized")
    if environ.get("HTTP_SEC_FETCH_SITE") not in (None,"same-origin"):
        reject("fetch_site","cross-site/opaque browser request denied",
               "actor_not_authorized")
    if environ.get("HTTP_SEC_FETCH_MODE") not in (None,"cors","same-origin"):
        reject("fetch_mode","unexpected write request context",
               "actor_not_authorized")
    if environ.get("QUERY_STRING"):
        reject("query","write routes do not accept query parameters")
    return origin


class LocalSREMutationAPI:
    """Strict WSGI mutation facade and optional M38 GET delegation.

    Read facade and write facade are separately opt-in. Host callbacks:
      actor_resolver(environ): authenticated actor from protected host session;
      csrf_verifier(environ, actor, origin, operation, csrf_token): True only for token
          bound to that same authenticated session, exact origin and action.
    A forged HTTP_X_ACTOR_ID/HTTP_X_PROJECT_ID is never used as authority.
    """

    def __init__(self,*,service,actor_resolver,csrf_verifier,
                 rate_limiter,read_api=None,enabled=False):
        if (not isinstance(service,TrustedSREMutationService) or
                not callable(actor_resolver) or not callable(csrf_verifier)
                or not callable(rate_limiter)
                or (read_api is not None and not isinstance(read_api,LocalSREReadAPI))
                or type(enabled) is not bool):
            raise ValueError("trusted service/session/CSRF/rate limit ports required")
        self.service=service
        self.actor_resolver=actor_resolver
        self.csrf_verifier=csrf_verifier
        self.rate_limiter=rate_limiter
        self.read_api=read_api
        self.enabled=enabled

    def __call__(self,environ,start_response):
        method=environ.get("REQUEST_METHOD")
        if method in ("GET","HEAD") and self.read_api is not None:
            return self.read_api(environ,start_response)
        if not self.enabled:
            return _response(start_response,"503 Service Unavailable",
                             {"error":"sre_mutations_disabled"})
        try:
            path=_safe_request(environ)
            if method!="POST":
                return _response(start_response,"405 Method Not Allowed",
                                 {"error":"post_only_mutation_api"},
                                 allow="POST")
            origin=_same_origin(environ)
            match=PROJECT_RE.fullmatch(path[len(ROOT):]) if path.startswith(ROOT) else None
            if match is None:
                return _response(start_response,"404 Not Found",
                                 {"error":"not_found"})
            project=project_id(match[1])
            resource=match[2] or ""
            matched=PATH.fullmatch(resource)
            if matched is None or matched[1] not in ALLOWED_OPERATIONS:
                return _response(start_response,"404 Not Found",
                                 {"error":"not_found"})
            # No DB opening, body parsing, domain service, or JSON echo until
            # host identity and anti-CSRF session proof are both validated.
            try:actor=_actor(self.actor_resolver(environ))
            except Exception:
                reject("actor","verified host session required","actor_not_authorized")
            token=environ.get("HTTP_X_CSRF_TOKEN")
            if (type(token) is not str or not 8<=len(token)<=MAX_CSRF_LENGTH
                or any(ord(c)<33 or ord(c)>126 for c in token)):
                reject("csrf","valid session-bound CSRF required",
                       "actor_not_authorized")
            try:
                valid=self.csrf_verifier(environ,actor,origin,matched[1],token)
            except Exception:
                valid=False
            if valid is not True:
                reject("csrf","host session CSRF validation failed",
                       "actor_not_authorized")
            # Operator host must enforce shared persistent per actor/project
            # rate policy; no process-local token bucket that resets on restart.
            try:
                permitted=self.rate_limiter(project,actor,matched[1])
            except Exception:
                permitted=False
            if permitted is not True:
                reject("rate_limit","trusted host denied mutation quota",
                       "rate_limited")
            request_key=environ.get("HTTP_IDEMPOTENCY_KEY")
            payload=_json_body(environ)
            response=self.service.perform(
                project,actor,matched[1],request_key,payload)
            return _response(start_response,"200 OK",response)
        except SREContractError as exc:
            if exc.code in ("rights_denied","actor_not_authorized",
                            "rights_expired","missing_provenance"):
                status,error="403 Forbidden","access_denied"
            elif exc.code in ("mutation_in_doubt","idempotency_conflict"):
                status,error="409 Conflict",exc.code
            elif exc.code=="rate_limited":
                status,error="429 Too Many Requests","rate_limited"
            else:
                status,error="400 Bad Request","invalid_request"
            return _response(start_response,status,{"error":error})
        except Exception:
            # Runtime exceptions may contain full DSN and private provider
            # details. Never return the exception or JSON request body.
            return _response(start_response,"503 Service Unavailable",
                             {"error":"temporarily_unavailable"})


def create_mutation_loopback_server(app,port):
    """Return stopped opt-in WSGI server; never bind a public interface."""
    from wsgiref.simple_server import make_server
    if not isinstance(app,LocalSREMutationAPI) or not app.enabled:
        raise ValueError("explicit enabled mutation host required")
    if type(port) is not int or not 0<=port<=65535:
        raise ValueError("invalid local port")
    return make_server("127.0.0.1",port,app)


__all__=["VERSION","MAX_JSON_BYTES","LocalSREMutationAPI",
         "create_mutation_loopback_server"]
