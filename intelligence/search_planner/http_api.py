"""SP-13: local-owner authenticated, strictly bounded HTTP API for SP01-SP12.

Mount through app.Handler AFTER existing Host/Origin/X-ContentMachine checks.
POST additionally requires local-loopback origin AND constant-time owner-token
comparison. A caller can never supply authorization callbacks, DB ports,
provider transports, Collector receipts or a validated QV snapshot.

No background jobs on import, no provider calls from GET, no auto-spend.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import threading
from urllib.parse import parse_qs

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid
from .contracts import publication_uuid
from .generation_workflow import GenerationWorkflow
from .operations import GenerationExecutionController
from .task_planner import TaskPlanner
from .service import SearchPlannerService

API_PREFIX = re.compile(r"/api/projects/([0-9a-f]{32})/search-planner(?:/(.*))?\Z")
MAX_JSON_BODY = 32_768
MAX_QUERY_BYTES = 2_048
_PAID_ONE_BATCH_GATE = threading.BoundedSemaphore(value=1)
OWNER_HEADER = "X-CM-SP-Owner-Token"
ALLOWED_OWNER_ACTIONS = frozenset({
    "search_planner.generation.authorize",
    "search_planner.generation.revoke",
    "search_planner.generation.execute_paid_batch",
    "search_planner.generation.recover",
    "search_planner.generation.release_unsubmitted",
    "search_planner.generation.cancel",
    "search_planner.plan.commit_held",
    "search_planner.handoff.deliver",
})
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
KEY = re.compile(r"[A-Za-z0-9_.:-]{12,128}\Z")


def _error(handler, code: int, message: str) -> None:
    handler.json_response(code, {
        "ok":False,"schema_version":"search-planner-http-response/v1",
        "error":message[:320],
    })


def _local_project(project_id: str, *, project_lookup=None) -> str:
    pid = _pid(project_id)
    if project_lookup is None:
        from projects import get_project
        project_lookup = get_project
    local = project_lookup(pid)
    if type(local) is not dict or local.get("id") != pid:
        raise PipelineError("Project not found in the local owner workspace", 404)
    return pid


def _local_transport(handler, *, mutation: bool) -> None:
    """Recheck dangerous SP mutations even when invoked outside app.Handler."""
    try:
        peer = handler.client_address[0]
        loopback = ipaddress.ip_address(peer).is_loopback
    except (AttributeError, IndexError, TypeError, ValueError):
        loopback = False
    if not loopback:
        raise PipelineError("Search Planner API is local-only", 403)
    authorized = getattr(handler, "authorized", None)
    if not callable(authorized) or authorized(mutation=mutation) is not True:
        raise PipelineError("Search Planner same-origin check failed", 403)


def _owner_guard(handler, project_id: str):
    _local_transport(handler, mutation=True)
    secret = os.environ.get("CM_SP_OWNER_TOKEN", "")
    presented = handler.headers.get(OWNER_HEADER, "")
    # Env secret must be provisioned locally, >=32 high-entropy characters.
    # No token is returned in GET, logged or persisted in any SP event.
    if (type(secret) is not str or not 32 <= len(secret) <= 256 or
            type(presented) is not str or
            len(presented) != len(secret) or
            not hmac.compare_digest(secret, presented)):
        raise PipelineError("Search Planner owner authentication required", 403)

    def verified_owner(action, pid, object_id):
        return (action in ALLOWED_OWNER_ACTIONS and
                pid == project_id and
                type(object_id) is str and len(object_id) == 36 and
                UUID.fullmatch(object_id) is not None)
    return verified_owner


def _make_service(owner_guard=None):
    """Assemble ONLY server-side ports. A default GET is strictly non-spending."""
    if owner_guard is None:
        return SearchPlannerService()
    return SearchPlannerService(
        generation_workflow=GenerationWorkflow(owner_action_guard=owner_guard),
        execution_controller=GenerationExecutionController(owner_action_guard=owner_guard),
        task_planner=TaskPlanner(owner_action_guard=owner_guard),
        handoff_owner_guard=owner_guard,
    )


def _json_body(handler, required=(), optional=()):
    headers = handler.headers
    if headers.get("Transfer-Encoding"):
        raise PipelineError("Chunked HTTP request bodies are not supported",400)
    mime = headers.get("Content-Type", "").split(";",1)[0].strip().lower()
    if mime != "application/json":
        raise PipelineError("Require application/json",415)
    raw_len = headers.get("Content-Length")
    if type(raw_len) is not str or re.fullmatch(r"[0-9]{1,5}", raw_len) is None:
        raise PipelineError("Content-Length required",411)
    size = int(raw_len)
    if not 2 <= size <= MAX_JSON_BODY:
        raise PipelineError("Search Planner JSON body exceeds safe limits",413)
    def distinct_keys(items):
        parsed={}
        for key,val in items:
            if key in parsed:
                raise ValueError("Repeated JSON object key")
            parsed[key]=val
        return parsed
    try:
        value = json.loads(
            handler.rfile.read(size).decode("utf-8"),
            object_pairs_hook=distinct_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")),
        )
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise PipelineError("Invalid Search Planner JSON body",400)
    if type(value) is not dict or not set(required).issubset(value) or (
            set(value) - set(required) - set(optional)):
        raise PipelineError("Invalid Search Planner request fields",400)
    return value


def _query(url, schema=None):
    if len(url.query) > MAX_QUERY_BYTES:
        raise PipelineError("Search Planner query too large",414)
    try:
        found = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise PipelineError("Invalid query parameters",400)
    expected = schema if schema is not None else set()
    if set(found) - set(expected) or any(len(values) != 1 for values in found.values()):
        raise PipelineError("Unsupported or duplicated Search Planner query parameters",400)
    return {k:vs[0] for k,vs in found.items()}


def _age(raw, *, default=900):
    if raw is None:
        return default
    if type(raw) is not int or not 1 <= raw <= 86400:
        raise PipelineError("Invalid recovery age",422)
    return raw


def _cap(raw, *, default, upper):
    if raw is None:
        return default
    if type(raw) is not int or not 1 <= raw <= upper:
        raise PipelineError("Invalid bounded Search Planner limit",422)
    return raw


def _ident(value: str) -> str:
    return publication_uuid(value)


def _key(value):
    if type(value) is not str or KEY.fullmatch(value) is None:
        raise PipelineError("Invalid idempotency key",422)
    return value


def _output(handler, result, *, code=200):
    if hasattr(result,"to_dict") and callable(result.to_dict):
        result=result.to_dict()
    # Ensure an injected backend cannot silently return a non-JSON secret
    # object or non-finite value to the HTTP server.
    try:
        json.dumps(result,allow_nan=False,ensure_ascii=False)
    except (TypeError, ValueError):
        raise PipelineError("Search Planner backend returned invalid JSON",503)
    handler.json_response(code, {
        "ok":True,"schema_version":"search-planner-http-response/v1",
        "result":result,
    })


def _dispatch_get(svc,pid,path,params):
    segments = path.split("/") if path else ["status"]
    if segments == ["status"]:
        _query(params)
        return svc.status()
    if segments == ["capabilities"]:
        _query(params)
        return svc.source_capabilities(pid)
    if segments == ["publications"]:
        values=_query(params,{"limit"})
        limit=_cap(int(values["limit"]) if values.get("limit","").isdigit() else (
            "bad" if "limit" in values else None),default=30,upper=50)
        return svc.approved_publications(pid,limit=limit)
    if len(segments)==2 and segments[0]=="generation":
        _query(params)
        return svc.get_generation(pid,_ident(segments[1]))
    if len(segments)==3 and segments[0]=="generation":
        _query(params)
        gen=_ident(segments[1])
        reads={
            "candidates":svc.get_generation_candidates,
            "budget":svc.generation_budget,
            "execution":svc.generation_execution_status,
            "preflight":svc.preflight_generation_execution,
        }
        if segments[2] in reads:
            return reads[segments[2]](pid,gen)
    if len(segments)==3 and segments[0]=="qv" and segments[2] in ("validated","links"):
        _query(params)
        vid=_ident(segments[1])
        return (svc.get_validated_queries(pid,vid) if segments[2]=="validated"
                else svc.get_validated_query_links(pid,vid))
    if len(segments)==3 and segments[0]=="inbox" and segments[2]=="status":
        _query(params)
        return svc.get_handoff_status(pid,_ident(segments[1]))
    if len(segments)==3 and segments[0]=="tasks" and segments[2]=="preview":
        _query(params)
        return svc.preview_source_tasks(pid,_ident(segments[1]))
    if len(segments)==3 and segments[0]=="handoff" and segments[2]=="preview":
        values=_query(params,{"chunk_size"})
        size=_cap(int(values["chunk_size"]) if values.get("chunk_size","").isdigit() else (
            "bad" if "chunk_size" in values else None),default=50,upper=50)
        return svc.preview_verified_handoff(pid,_ident(segments[1]),chunk_size=size)
    if len(segments)==3 and segments[0]=="generation" and segments[2]=="stale-reservations":
        values=_query(params,{"older_than_seconds"})
        age=_age(int(values["older_than_seconds"]) if values.get("older_than_seconds","").isdigit() else (
            "bad" if "older_than_seconds" in values else None))
        return svc.stale_generation_reservations(pid,_ident(segments[1]),older_than_seconds=age)
    raise PipelineError("Unknown read-only Search Planner endpoint",404)


def _dispatch_post(svc,pid,path,body,*,paid_enabled=False):
    parts=path.split("/") if path else []
    if parts == ["contexts","preview"]:
        _expect(body,{"publication_id","search_policy"})
        return svc.preview_contexts(pid,_ident(body["publication_id"]),body["search_policy"])
    if parts == ["capabilities","match"]:
        _expect(body,{"query"})
        return svc.match_query_capabilities(pid,body["query"])
    if parts == ["generation","configs"]:
        _expect(body,{"configuration"})
        return svc.register_generation_config(pid,body["configuration"])
    if parts == ["generation","plan"]:
        _expect(body,{"publication_id","config_id","idempotency_key"})
        return svc.plan_generation(pid,_ident(body["publication_id"]),
                                   _ident(body["config_id"]),_key(body["idempotency_key"]))
    if parts == ["qv","validate"]:
        _expect(body,{"source_outbox_id","policy"})
        model_path=os.environ.get("CM_SP_EMBEDDING_MODEL_PATH", "").strip()
        if not model_path:
            raise PipelineError("Provision a pinned local multilingual embedding model first",503)
        from .query_validation.embedding import SentenceTransformerEmbeddingProvider
        from .query_validation.service import validate_generation_outbox
        provider=SentenceTransformerEmbeddingProvider(model_path)
        return validate_generation_outbox(
            pid,_ident(body["source_outbox_id"]),policy=body["policy"],
            embedding_provider=provider,allow_test_embeddings=False,
        )
    if parts == ["handoff","drain"]:
        _expect(body,set(),{"limit"})
        return svc.ingest_pending_qv_handoffs(pid,limit=_cap(body.get("limit"),default=10,upper=50))
    if len(parts)==3 and parts[0]=="generation":
        gen=_ident(parts[1])
        action=parts[2]
        if action=="quote":
            _expect(body,set())
            return svc.quote_generation(pid,gen)
        if action=="consent":
            _expect(body,{"quote_id","authorized_usd"})
            if type(body["authorized_usd"]) is not str:
                raise PipelineError("Financial consent must be explicit decimal string",422)
            return svc.authorize_generation(pid,gen,_ident(body["quote_id"]),
                                            body["authorized_usd"])
        if action=="execute-one":
            _expect(body,{"idempotency_key"})
            if not paid_enabled:
                raise PipelineError("Paid batch HTTP execution disabled by operator",403)
            key=_key(body["idempotency_key"])
            # ThreadingHTTPServer serves unrelated requests on separate
            # threads. Never launch an unbounded parallel set of paid calls.
            if not _PAID_ONE_BATCH_GATE.acquire(blocking=False):
                raise PipelineError("One paid SP batch is already executing",409)
            try:
                from .http_paid import one_paid_batch_with_idempotency
                return one_paid_batch_with_idempotency(
                    pid,gen,key,executor=svc.execute_generation_batch,
                )
            finally:
                _PAID_ONE_BATCH_GATE.release()
        if action=="recover":
            _expect(body,set(),{"older_than_seconds"})
            return svc.recover_generation_dispatches(
                pid,gen,older_than_seconds=_age(body.get("older_than_seconds")))
        if action=="cancel":
            _expect(body,set())
            return svc.cancel_generation(pid,gen)
    if len(parts)==4 and parts[0]=="generation" and parts[2]=="reservation":
        gen=_ident(parts[1])
        attempt=_ident(parts[3])
        _expect(body,set(),{"older_than_seconds"})
        return svc.release_unsubmitted_generation_attempt(
            pid,gen,attempt,older_than_seconds=_age(body.get("older_than_seconds")))
    if len(parts)==3 and parts[0]=="consent" and parts[2]=="revoke":
        _expect(body,set())
        return svc.revoke_generation_consent(pid,_ident(parts[1]))
    if len(parts)==3 and parts[0]=="tasks" and parts[2] in ("preview","commit"):
        _expect(body,set() if parts[2]=="preview" else {"idempotency_key"},{"limits"})
        vid=_ident(parts[1])
        lim=body.get("limits")
        if parts[2]=="preview":
            return svc.preview_source_tasks(pid,vid,limits=lim)
        return svc.commit_held_source_plan(
            pid,vid,_key(body["idempotency_key"]),limits=lim)
    if len(parts)==3 and parts[0]=="handoff" and parts[2] in ("preview","ingest"):
        vid=_ident(parts[1])
        if parts[2]=="preview":
            _expect(body,set(),{"chunk_size"})
            return svc.preview_verified_handoff(
                pid,vid,chunk_size=_cap(body.get("chunk_size"),default=50,upper=50))
        _expect(body,set())
        return svc.ingest_verified_qv_handoff(pid,vid)
    if len(parts)==3 and parts[0]=="inbox" and parts[2] in ("deliver","reconcile"):
        eid=_ident(parts[1])
        if parts[2]=="deliver":
            _expect(body,set())
            return svc.deliver_next_handoff_chunk(pid,eid)
        _expect(body,{"chunk_index"})
        index=body["chunk_index"]
        if type(index) is not int or not 0<=index<480:
            raise PipelineError("Invalid Collector reconciliation index",422)
        return svc.reconcile_handoff_chunk(pid,eid,index)
    raise PipelineError("Unknown Search Planner mutation endpoint",404)


def _expect(data, required, optional=()):
    if type(data) is not dict or not set(required).issubset(data) or (
            set(data)-set(required)-set(optional)):
        raise PipelineError("Unknown or missing Search Planner request fields",400)


def handle_get(handler,url,*,service_factory=None,project_lookup=None):
    match=API_PREFIX.fullmatch(url.path)
    if match is None:
        return False
    try:
        _local_transport(handler,mutation=False)
        pid=_local_project(match.group(1),project_lookup=project_lookup)
        svc=(service_factory(None) if service_factory is not None
             else _make_service())
        result=_dispatch_get(svc,pid,match.group(2) or "status",url)
        _output(handler,result)
    except PipelineError as exc:
        _error(handler,exc.http_status,str(exc))
    except Exception:
        _error(handler,503,"Search Planner unavailable; inspect local server logs")
    return True


def handle_post(handler,route,*,service_factory=None,project_lookup=None):
    match=API_PREFIX.fullmatch(route)
    if match is None:
        return False
    try:
        _local_transport(handler,mutation=True)
        pid=_local_project(match.group(1),project_lookup=project_lookup)
        guard=_owner_guard(handler,pid)
        body=_json_body(handler)
        svc=(service_factory(guard) if service_factory is not None
             else _make_service(guard))
        result=_dispatch_post(
            svc,pid,match.group(2) or "",body,
            paid_enabled=os.environ.get("CM_SP_PAID_HTTP_ENABLED")=="1",
        )
        _output(handler,result)
    except PipelineError as exc:
        _error(handler,exc.http_status,str(exc))
    except Exception:
        _error(handler,503,"Search Planner unavailable; inspect local server logs")
    return True
