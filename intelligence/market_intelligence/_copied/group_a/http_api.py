"""Project-scoped Group A local HTTP handlers. Inherits app.py host/origin guard."""
from __future__ import annotations
import json
import re
from urllib.parse import parse_qs
from . import repository
from .contracts import GroupAError
from .capabilities import capability_snapshot, require_live_handoff

PROJECT=r"[0-9a-f]{32}"
UUID=r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
BASE=re.compile(rf"/api/projects/({PROJECT})/group-a(?:/(.*))?\Z")


def _error(handler,exc):
    handler.json_response(exc.http_status,{"ok":False,"error":{
        "code":exc.code,"message":str(exc)}})


def handle_get(handler,url):
    match=BASE.fullmatch(url.path)
    if not match:
        return False
    project,suffix=match.groups()
    try:
        if suffix=="capabilities":
            handler.json_response(200,capability_snapshot())
            return True
        require_live_handoff()
        if suffix=="tasks":
            params=parse_qs(url.query)
            if set(params)!={"qdp_run_id"} or len(params["qdp_run_id"])!=1:
                raise GroupAError("invalid_query","Exact QDP run ID required")
            result=repository.list_tasks(project,params["qdp_run_id"][0])
        elif suffix and re.fullmatch(r"tasks/"+UUID,suffix):
            result=repository.task_details(project,suffix.split("/")[1])
        else:
            raise GroupAError("not_found","Group A route not found",404)
        handler.json_response(200,result)
    except GroupAError as exc:
        _error(handler,exc)
    except Exception:
        handler.json_response(503,{"ok":False,"error":{
            "code":"db_unavailable","message":"Group A unavailable"}})
    return True


def handle_post(handler,path):
    match=BASE.fullmatch(path)
    if not match:
        return False
    project,suffix=match.groups()
    if suffix!="tasks":
        handler.json_response(404,{"ok":False,"error":{
            "code":"not_found","message":"Group A route not found"}})
        return True
    try:
        require_live_handoff()
        size=int(handler.headers.get("Content-Length","0"))
        if not 2<=size<=4096:
            raise GroupAError("invalid_body","Group A accepts at most 4096 bytes",413)
        payload=json.loads(handler.rfile.read(size).decode("utf-8"))
        if type(payload) is not dict or set(payload)!={"qdp_run_id","video_id","config"}:
            raise GroupAError("invalid_body","Expected qdp_run_id,video_id,config")
        result=repository.create_task(project,payload["qdp_run_id"],
                                      payload["video_id"],payload["config"])
        from .worker import start_worker
        start_worker()
        handler.json_response(202,result)
    except GroupAError as exc:
        _error(handler,exc)
    except (ValueError,KeyError,TypeError,UnicodeError):
        handler.json_response(400,{"ok":False,"error":{
            "code":"invalid_body","message":"Malformed Group A request"}})
    except Exception:
        handler.json_response(503,{"ok":False,"error":{
            "code":"db_unavailable","message":"Group A unavailable"}})
    return True


# GA-MOD-21: separate versioned endpoint facade. The legacy A1
# handle_get/handle_post above remain untouched for compatibility and are
# NOT auto-registered on the host. GA21 has its own complete security gate.
from .observability import (
    GroupAMonitor, ObservationDenied, safe_project, safe_run,
    safe_limit, append_operator_event,
)
from .task_queue import (
    install_run_dag, pause_run, retry_local_step,
)
from .invalidation import cancel_run
from .cost_resources import RuntimeDenied, ResourceBudget
from .contracts_v1 import media_manifest_v1

V21=re.compile(
    rf"/api/projects/({PROJECT})/group-a/v1(?:/(.*))?\Z"
)
RUN_SUB=re.compile(
    rf"runs/({UUID})(?:/(steps|events|measurements|start|pause|resume|cancel|retry))?\Z"
)
MAX_BODY=4096
MAX_RESPONSE=65536


class GroupAHTTPFacade:
    """Opt-in local HTTP contract with per-request project + current QDP checks.

    authorize_project(project_id,action) MUST return an authenticated
    {"allowed":True,"actor_ref_sha256":"..."} from the owning host session.
    resolve_manifest(project_id,run_id) must authenticate GA04/QDP and
    return *exact* GA05 media-manifest/v1, not an unverified client pointer.
    verify_current(scope) must authenticate live QDP/run/media rights.
    """
    def __init__(self, *, connect, authorize_project, verify_current,
                 resolve_manifest, verify_authority, verify_result,
                 allowed_hosts=None,budget=None):
        from .observability import SHA
        if any(not callable(x) for x in (
            connect,authorize_project,verify_current,
            resolve_manifest,verify_authority,verify_result
        )):
            raise ObservationDenied("host_security_ports_required",503)
        self.connect=connect
        self.authorize_project=authorize_project
        self.verify_current=verify_current
        self.resolve_manifest=resolve_manifest
        self.verify_authority=verify_authority
        self.budget=ResourceBudget() if budget is None else budget
        if not isinstance(self.budget,ResourceBudget):
            raise ObservationDenied("invalid_runtime_budget")
        hosts=("127.0.0.1:8765","localhost:8765") if allowed_hosts is None else allowed_hosts
        if (type(hosts) not in (tuple,list) or not hosts or len(hosts)>8
            or any(type(v) is not str or not re.fullmatch(
                r"(?:127\.0\.0\.1|localhost):[1-9][0-9]{1,4}",v
            ) for v in hosts)):
            raise ObservationDenied("invalid_local_host_config",503)
        self.allowed_hosts=frozenset(h.lower() for h in hosts)
        self.monitor=GroupAMonitor(connect,verify_current,verify_result)

    def _auth(self,handler,project,method):
        from .observability import SHA
        host=handler.headers.get("Host","").lower()
        origin=handler.headers.get("Origin","")
        if (host not in self.allowed_hosts or
            (origin and origin not in {"http://"+h for h in self.allowed_hosts})):
            raise ObservationDenied("local_origin_required",403)
        if method=="POST":
            if handler.headers.get("X-ContentMachine")!="1":
                raise ObservationDenied("csrf_header_required",403)
            if handler.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
                raise ObservationDenied("json_content_type_required",415)
        actor=self.authorize_project(project,"write" if method=="POST" else "read")
        if (type(actor) is not dict or actor.get("allowed") is not True
            or type(actor.get("actor_ref_sha256")) is not str or
            SHA.fullmatch(actor["actor_ref_sha256"]) is None):
            raise ObservationDenied("project_access_denied",403)
        return actor["actor_ref_sha256"]

    def _json_body(self,handler):
        raw=handler.headers.get("Content-Length")
        if raw is None or not raw.isascii() or not raw.isdecimal():
            raise ObservationDenied("invalid_body_size",400)
        size=int(raw)
        if size<2 or size>MAX_BODY:
            raise ObservationDenied("invalid_body_size",413)
        try:
            data=json.loads(handler.rfile.read(size).decode("utf-8"))
        except (UnicodeError,ValueError) as exc:
            raise ObservationDenied("malformed_json",400) from exc
        if type(data) is not dict:
            raise ObservationDenied("body_must_be_object",400)
        return data

    @staticmethod
    def _params(url,allowed):
        params=parse_qs(url.query,keep_blank_values=True)
        if set(params)-set(allowed) or any(len(v)!=1 for v in params.values()):
            raise ObservationDenied("invalid_query")
        return {key:v[0] for key,v in params.items()}

    def _response(self,handler,status,data):
        # Stable bounded wire; never log body, origin, client address or
        # provider error. The host's json_response adds no-store+nosniff.
        if len(json.dumps(data,ensure_ascii=False,default=str).encode("utf-8"))>MAX_RESPONSE:
            raise ObservationDenied("response_limit_reached",413)
        handler.json_response(status,data)

    def _post(self,project,run_id,action,body,actor):
        from .observability import safe_run
        safe_run(run_id)
        if action=="start":
            if body:
                raise ObservationDenied("start_body_must_be_empty")
            manifest=media_manifest_v1(self.resolve_manifest(project,run_id))
            if manifest["project_id"]!=project:
                raise ObservationDenied("foreign_project_manifest",403)
            with self.connect() as conn:
                with conn.transaction():
                    outcome=install_run_dag(
                        conn,manifest,run_id=run_id,
                        verify_current=self.verify_current,budget=self.budget
                    )
                    append_operator_event(
                        conn,project=project,run_id=run_id,
                        event_kind="dag_scheduled",actor_ref_sha256=actor
                    )
            return 202,{"ok":True,"schema_version":"ga21-action/v1",
                        "state":"scheduled_explicit_worker_required",
                        "run_id":run_id,"stage_count":outcome["stage_count"]}
        if action in ("pause","resume","cancel"):
            if body:
                raise ObservationDenied("action_body_must_be_empty")
            with self.connect() as conn:
                with conn.transaction():
                    if action=="cancel":
                        result=cancel_run(
                            conn,run_id=run_id,verify_authority=self.verify_authority
                        )
                        event="run_cancelled"
                    else:
                        result=pause_run(
                            conn,run_id,paused=action=="pause",
                            verify_current=self.verify_current
                        )
                        event="run_paused" if action=="pause" else "run_resumed"
                    # Deny all foreign project run IDs, even if the host
                    # accidentally accepted an otherwise valid QDP run ID.
                    lookup=conn.execute(
                        "SELECT 1 FROM ga_runs WHERE id=%s::uuid AND project_id=%s",
                        (run_id,project)
                    ).fetchone()
                    if lookup is None:
                        raise ObservationDenied("foreign_project_run",404)
                    append_operator_event(
                        conn,project=project,run_id=run_id,
                        event_kind=event,actor_ref_sha256=actor
                    )
            return 200,{"ok":True,"schema_version":"ga21-action/v1",
                        "run_id":run_id,"state":result}
        if action=="retry":
            if set(body)!={"step_key"}:
                raise ObservationDenied("retry_step_key_required")
            with self.connect() as conn:
                with conn.transaction():
                    if conn.execute(
                        "SELECT 1 FROM ga_runs WHERE id=%s::uuid AND project_id=%s",
                        (run_id,project)
                    ).fetchone() is None:
                        raise ObservationDenied("foreign_project_run",404)
                    result=retry_local_step(
                        conn,run_id=run_id,step_key=body["step_key"],
                        verify_current=self.verify_current
                    )
                    append_operator_event(
                        conn,project=project,run_id=run_id,
                        event_kind="local_step_retried",
                        actor_ref_sha256=actor,step_id=result["step_id"]
                    )
            return 202,{"ok":True,"schema_version":"ga21-action/v1",
                        "run_id":run_id,"state":"queued",
                        "step_key":result["step_key"]}
        raise ObservationDenied("action_not_supported",405)

    def handle(self,handler,url,method):
        """Returns False if the request is outside GA21's own route prefix."""
        import urllib.parse
        if type(url) is str:
            url=urllib.parse.urlparse(url)
        route=V21.fullmatch(url.path)
        if not route:
            return False
        project,suffix=route.groups()
        try:
            if method not in ("GET","POST"):
                raise ObservationDenied("method_not_allowed",405)
            actor=self._auth(handler,project,method)
            if method=="GET":
                if suffix=="capabilities":
                    if url.query:
                        raise ObservationDenied("invalid_query")
                    from .capabilities import capability_snapshot
                    status=capability_snapshot()
                    # Isolated GA21 mount does not imply background workers
                    # or direct QDP/B readiness. Never reflect host details.
                    data={"schema_version":"ga21-capabilities/v1",
                          "api_state":"host_mounted",
                          "worker_start":"explicit_only",
                          "paid_auto_retry":False,
                          "live_qdp_rights":"external_verifier_required",
                          "readiness":status}
                    self._response(handler,200,data)
                    return True
                if suffix=="runs":
                    q=self._params(url,{"limit","cursor"})
                    data=self.monitor.runs(
                        project,limit=safe_limit(int(q.get("limit","20")))
                        if q.get("limit","20").isascii() and q.get("limit","20").isdecimal()
                        else safe_limit(0),
                        cursor=q.get("cursor")
                    )
                    self._response(handler,200,{"ok":True,**data})
                    return True
                sub=RUN_SUB.fullmatch(suffix or "")
                if not sub:
                    raise ObservationDenied("route_not_found",404)
                run_id,resource=sub.groups()
                if resource in ("start","pause","resume","cancel","retry"):
                    raise ObservationDenied("method_not_allowed",405)
                if resource in ("events","steps"):
                    q=self._params(url,{"limit","cursor"})
                    raw=q.get("limit","20")
                    if not raw.isascii() or not raw.isdecimal():
                        raise ObservationDenied("invalid_page_limit")
                    params={"limit":safe_limit(int(raw)),"cursor":q.get("cursor")}
                    data=(self.monitor.events if resource=="events" else
                          self.monitor.steps)(project,run_id,**params)
                elif resource=="measurements":
                    q=self._params(url,{"limit"})
                    raw=q.get("limit","20")
                    if not raw.isascii() or not raw.isdecimal():
                        raise ObservationDenied("invalid_page_limit")
                    data=self.monitor.measurements(
                        project,run_id,limit=safe_limit(int(raw))
                    )
                else:
                    if url.query:
                        raise ObservationDenied("invalid_query")
                    data=self.monitor.overview(project,run_id)
                self._response(handler,200,{"ok":True,**data})
                return True
            # POST: no query or unadvertised action; body is bounded and
            # interpreted only after host Project + Host/Origin/CSRF checks.
            if url.query:
                raise ObservationDenied("invalid_query")
            sub=RUN_SUB.fullmatch(suffix or "")
            if sub is None or sub.group(2) not in (
                "start","pause","resume","cancel","retry"
            ):
                raise ObservationDenied("route_not_found",404)
            body=self._json_body(handler)
            status,data=self._post(project,sub.group(1),sub.group(2),body,actor)
            self._response(handler,status,data)
        except ObservationDenied as exc:
            handler.json_response(exc.status,{"ok":False,"error":{"code":exc.code}})
        except RuntimeDenied as exc:
            handler.json_response(409,{"ok":False,"error":{"code":exc.code}})
        except (ValueError,TypeError,KeyError):
            handler.json_response(400,{"ok":False,"error":{"code":"invalid_request"}})
        except Exception:
            # No DB/OS/provider text or private project/media paths leak.
            handler.json_response(503,{"ok":False,"error":{"code":"ga_unavailable"}})
        return True
