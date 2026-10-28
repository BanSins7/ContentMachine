"""PIE-MOD-34: read-only, source-rights-rechecked Pattern Explorer projections.

No raw registry JSON, insight draft text, reviews, research row IDs or model
data crosses this API. No live reader/verified session => no data. The UI is
NOT a release or scientific approval gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable,Protocol
import re

from ..contracts import PIEBoundaryError
from ..intake.provenance import SourceEligibilityGate
from .pattern_queries import (
    RegistryQuery,CursorSigner,CurrentProjectAuthorizer,FreshUAEReader,
    query_pattern_registry,RedactedPattern,
)

SCHEMA="pie-pattern-explorer/v1"
PROJECT_RE=re.compile(r"[0-9a-f]{32}\Z")
ACTOR_RE=re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:@/-]{0,159}\Z")
SHA_RE=re.compile(r"[0-9a-f]{64}\Z")
VIEWS=frozenset(("patterns","datasets","candidates","validations",
                "results","insights","reviews","overview"))
STATUS_LABELS={
    "exploratory":"Предварительная закономерность",
    "rejected":"Исследовательский кандидат отклонён",
    "needs_review":"Недостаточно проверенных наблюдений",
}


class VerifiedActor(Protocol):
    """Host MUST authenticate actual session, never use query/header actor."""
    def verified_project_actor(self,request_handler,project_id:str)->str:...


@dataclass(frozen=True,slots=True)
class ExplorerPorts:
    actor:VerifiedActor
    connect:Callable
    authorizer:CurrentProjectAuthorizer
    reader:FreshUAEReader
    gate:SourceEligibilityGate
    signer:CursorSigner

    def __post_init__(self):
        if (self.actor is None or not callable(getattr(self.actor,"verified_project_actor",None))
            or not callable(self.connect)
            or self.authorizer is None
            or not callable(getattr(self.authorizer,"require_current_project_access",None))
            or self.reader is None
            or not callable(getattr(self.reader,"read_fresh_qualified",None))
            or type(self.gate) is not SourceEligibilityGate
            or type(self.signer) is not CursorSigner):
            raise PIEBoundaryError("pie_ports_unavailable","Missing real current authenticated ports")


def _check_actor(ports:ExplorerPorts,request_handler,project_id:str)->str:
    if type(ports) is not ExplorerPorts:
        raise PIEBoundaryError("pie_ports_unavailable","Runtime source authority unavailable")
    if type(project_id) is not str or not PROJECT_RE.fullmatch(project_id):
        raise PIEBoundaryError("pie_invalid_query","Exact project identifier required")
    try:
        actor=ports.actor.verified_project_actor(request_handler,project_id)
    except Exception:
        actor=None
    if type(actor) is not str or not ACTOR_RE.fullmatch(actor):
        raise PIEBoundaryError("pie_access_denied","Authenticated project identity required")
    try:
        authorized=ports.authorizer.require_current_project_access(project_id,actor)
    except Exception:
        authorized=False
    if authorized is not True:
        raise PIEBoundaryError("pie_access_denied","Current project membership required")
    return actor


def _safe_pattern(pattern:RedactedPattern)->dict:
    if type(pattern) is not RedactedPattern:
        raise PIEBoundaryError("pie_untrusted_view","Only stage26 safe pattern allowed")
    p=pattern.as_dict()
    return {
        "series_id":p["series_id"],"hypothesis_sha256":p["hypothesis_sha256"],
        "revision":p["revision"],"factor_id":p["factor_id"],
        "feature_family":p["feature_family"],"feature_name":p["feature_name"],
        "metric_kind":p["metric_kind"],"platform":p["platform"],
        "status":p["status"],"status_label":STATUS_LABELS.get(p["status"],"Неизвестный статус"),
        "matched_pair_count":p["matched_pair_count"],
        "supported_bin_count":p["supported_bin_count"],
        "descriptive_direction":p["descriptive_direction"],
        "descriptive_raw_mean":p["descriptive_raw_mean"],
        "descriptive_source_unit":p["descriptive_source_unit"],
        "sample_scope":"matched_discovery_only",
        "confidence":"not_calibrated",
        "inferential_p_value":None,"confidence_interval":None,
        "independent_holdout_confirmed":False,
        "causal_claim":False,"can_publish":False,
        "caveats":[
            "Наблюдаемая связь не означает причинность.",
            "Независимая проверка на holdout не подтверждена.",
            "Статистическая значимость и доверительные интервалы не калиброваны.",
            "Права доступа повторно проверяются при каждом запросе.",
        ],
        "suggested_test":"Предварительно зарегистрировать независимый эксперимент на разрешённой выборке.",
    }


def _positive(raw,default,high):
    if raw is None:return default
    if type(raw) is not str or not re.fullmatch(r"[0-9]{1,5}",raw):
        raise PIEBoundaryError("pie_invalid_query","Integer filter required")
    number=int(raw)
    if not 1<=number<=high:
        raise PIEBoundaryError("pie_invalid_query","Filter outside bounded range")
    return number


def _nonnegative(raw,default,high):
    if raw is None:return default
    if type(raw) is not str or not re.fullmatch(r"[0-9]{1,5}",raw):
        raise PIEBoundaryError("pie_invalid_query","Integer filter required")
    number=int(raw)
    if not 0<=number<=high:
        raise PIEBoundaryError("pie_invalid_query","Filter outside bounded range")
    return number


def _input_query(project:str,actor:str,query:dict)->RegistryQuery:
    fields={"status","platform","b_feature_name","hypothesis_sha256",
            "min_matched_pairs","min_supported_bins","page_size","cursor"}
    if (type(query) is not dict or set(query)-fields
        or any(type(x) is not str for x in query.values())):
        raise PIEBoundaryError("pie_invalid_query","Unknown/duplicate request parameters")
    return RegistryQuery(
        project_id=project,principal_id=actor,
        status=query.get("status"),platform=query.get("platform"),
        b_feature_name=query.get("b_feature_name"),
        hypothesis_sha256=query.get("hypothesis_sha256"),
        min_matched_pairs=_positive(query.get("min_matched_pairs"),1,100000),
        min_supported_bins=_nonnegative(query.get("min_supported_bins"),0,16),
        page_size=_positive(query.get("page_size"),20,80),
        cursor=query.get("cursor"),
    )


def _envelope(project:str,view:str,items:list,*,page=None,state=None,meta=None):
    if state is None:state="candidate_data" if items else "not_ready"
    return {
        "schema_version":SCHEMA,"ok":True,"project_id":project,
        "view":view,"state":state,"items":items,
        "next_cursor":page.next_cursor if page else None,
        "page_count":len(items),
        "page_scope_only":True,
        "publication_allowed":False,"causal_claim":False,
        "data_provenance":"current_rights_checked_registry_candidate_only",
        "meta":meta or {},
    }


def _read_summaries(connection,project_id:str,patterns:list[dict],view:str)->list[dict]:
    """Never join historical/raw insight text or model output to HTTP."""
    if not patterns:return []
    permitted={p["series_id"]:p for p in patterns}
    rows=connection.execute(
        """SELECT g.series_id,g.generation_sha256,g.source_revision,
                  v.verdict,v.reason_code,
                  (SELECT count(*) FROM pie_candidate_insight_review_events e
                    WHERE e.project_id=g.project_id
                      AND e.generation_sha256=g.generation_sha256) AS event_count
           FROM pie_candidate_insight_generations g
           LEFT JOIN LATERAL (
               SELECT a.verdict,a.reason_code
                 FROM pie_candidate_insight_verification_events a
                WHERE a.project_id=g.project_id
                  AND a.generation_sha256=g.generation_sha256
                ORDER BY a.created_at DESC,a.verification_sha256 DESC LIMIT 1
           ) v ON TRUE
           WHERE g.project_id=%s AND g.series_id=ANY(%s)
           ORDER BY g.created_at DESC,g.generation_sha256 DESC LIMIT 80""",
        (project_id,list(permitted))).fetchall()
    output=[];seen=set()
    for row in rows:
        series,gen,revision,verdict,reason,events=row
        original=permitted.get(series)
        if (original is None or original["revision"]!=revision
            or series in seen or type(gen) is not str or not SHA_RE.fullmatch(gen)):
            continue
        seen.add(series)
        # Exact current source rights are established by the same response's
        # stage26 redacted page; no historic revision or unauthorized ID shows.
        output.append({
            "series_id":series,"revision":revision,
            "generation_sha256":gen,
            "verification_state":verdict or "not_checked",
            "reason_code":reason if verdict=="quarantined" else None,
            "internal_review_event_count":events if type(events) is int and events>=0 else 0,
            "insight_text_available":False,
            "independent_holdout_confirmed":False,
            "causal_claim":False,"can_publish":False,
            "review_stage":"internal_candidate_only",
        })
    return output


def explore(connection,project_id:str,view:str,actor_id:str,query:dict,
            ports:ExplorerPorts,*,now:datetime|None=None)->dict:
    """Authenticated actor already obtained from host. Recheck membership now.

    The source must be currently authorized for ALL original rows. Old/stale
    versions are never returned from the page. No synthetic source claims.
    """
    if view not in VIEWS or type(actor_id) is not str or not ACTOR_RE.fullmatch(actor_id):
        raise PIEBoundaryError("pie_invalid_query","Unsupported viewer or actor")
    # Fresh stage26 rights are mandatory regardless of requested view;
    # even capability/empty responses must never bypass project auth.
    safe_query=_input_query(project_id,actor_id,query)
    page=query_pattern_registry(connection,safe_query,ports.authorizer,
                                ports.reader,ports.gate,ports.signer,now=now)
    if (page.project_id!=project_id or page.source_rights_revalidated is not True):
        raise PIEBoundaryError("pie_rights_unavailable","Current source visibility absent")
    patterns=[_safe_pattern(p) for p in page.items]
    if view=="datasets":
        # No direct access to raw sources/dataset manifests in HTTP.
        return _envelope(project_id,view,[],page=page,state="not_ready",
            meta={"reason":"dataset_source_metadata_not_releasable",
                  "current_source_rights_required":True})
    if view=="overview":
        status={k:sum(x["status"]==k for x in patterns) for k in STATUS_LABELS}
        return _envelope(project_id,view,patterns,page=page,
            meta={"descriptive_pattern_page_count":len(patterns),
                  "candidate_status_counts_on_page":status,
                  "matched_pair_count_on_page":sum(p["matched_pair_count"] for p in patterns),
                  "note":"Totals apply only to currently visible page, not the full corpus."})
    if view in ("insights","reviews"):
        items=_read_summaries(connection,project_id,patterns,view)
        return _envelope(project_id,view,items,page=page)
    if view in ("validations","results"):
        # Registry status is descriptive candidate validation, not external
        # significance test. Do not expose legacy raw validation tables.
        items=[{**p,"validation_kind":"unverified_descriptive_quality_gate",
                "calibrated_effect":None,"p_value":None,
                "confidence_interval":None,"independent_holdout_confirmed":False}
               for p in patterns]
        return _envelope(project_id,view,items,page=page)
    # patterns and candidates share one safe source-of-truth projection.
    return _envelope(project_id,view,patterns,page=page)


def explore_for_request(request_handler,project_id:str,view:str,query:dict,
                        ports:ExplorerPorts,*,now=None):
    actor=_check_actor(ports,request_handler,project_id)
    try:
        # A request-scoped connection, no shared unauthenticated global cursor.
        with ports.connect() as connection:
            return explore(connection,project_id,view,actor,query,ports,now=now)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError("pie_data_unavailable",
                               "Current evidence could not be verified") from exc
