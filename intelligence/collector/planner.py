"""Offline preview of TikTok discovery queries from an *approved* RTB tree.

No network, API credentials, provider prices, background jobs or media access.
Plans require a caller-verified immutable taxonomy publication.
"""
from __future__ import annotations
import hashlib
import json
import re

PLAN_VERSION = "collector-preview-v1"
MAX_NODES = 48

class CollectorPlanError(ValueError):
    pass

def _sentence(value, label, maxlen=160):
    if not isinstance(value, str):
        raise CollectorPlanError(f"{label}: expected text")
    value = " ".join(value.split())
    if not value or len(value) > maxlen or any(ord(c) < 32 for c in value):
        raise CollectorPlanError(f"{label}: invalid length or text")
    return value

def build_preview(project_id, taxonomy_version, published_nodes, *,
                  publication_verified=False, max_queries=12, results_per_query=5):
    """Create bounded Actor inputs, NEVER dispatch an Actor.

    `publication_verified` must be set by a caller that read immutable
    tb_deep_publications for the same project and RTB version. Data supplied
    by a model or a browser draft MUST NOT pass this gate.
    """
    if publication_verified is not True:
        raise CollectorPlanError("A confirmed, immutable human-approved RTB publication is required")
    if not isinstance(project_id,str) or not re.fullmatch(r"[0-9a-f]{32}",project_id):
        raise CollectorPlanError("Project ID must be a valid project scope")
    if type(taxonomy_version) is not int or taxonomy_version<1:
        raise CollectorPlanError("Taxonomy version must be a positive integer")
    if type(max_queries) is not int or not 1<=max_queries<=20:
        raise CollectorPlanError("Select between 1 and 20 search queries")
    if type(results_per_query) is not int or not 1<=results_per_query<=25:
        raise CollectorPlanError("Select 1-25 search results per query")
    if not isinstance(published_nodes,list) or not 2<=len(published_nodes)<=MAX_NODES:
        raise CollectorPlanError("Approved hierarchy requires 2-48 nodes")

    index={}
    for raw in published_nodes:
        if not isinstance(raw,dict):
            raise CollectorPlanError("Malformed hierarchy node")
        key=_sentence(raw.get("id"),"Node ID",80)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,79}",key) or key in index:
            raise CollectorPlanError("Non-unique or invalid stable node ID")
        kind=raw.get("kind")
        if kind not in ("direction","branch","hypothesis"):
            raise CollectorPlanError("Unsupported hierarchy level")
        name=_sentence(raw.get("name"),"Node title")
        index[key]={"id":key,"name":name,"kind":kind,
                    "parent_id":raw.get("parent_id"),
                    "source_fact_ids":list(raw.get("source_fact_ids") or [])}
    roots=0
    for n in index.values():
        parent=n["parent_id"]
        if parent is None:
            roots+=1
            if n["kind"]!="direction":
                raise CollectorPlanError("Only a direction can be a root")
        elif not isinstance(parent,str) or parent not in index or parent==n["id"]:
            raise CollectorPlanError("Missing or invalid parent node")
        visited=set()
        current=n
        while current["parent_id"] is not None:
            if current["id"] in visited:
                raise CollectorPlanError("Hierarchy contains a cycle")
            visited.add(current["id"])
            current=index[current["parent_id"]]
    if roots<2:
        raise CollectorPlanError("At least two directions required")

    hypotheses=[n for n in index.values() if n["kind"]=="hypothesis"]
    if not hypotheses:
        raise CollectorPlanError("No approved research hypotheses to search")
    selected=[]
    seen=set()
    for leaf in hypotheses:
        ancestors=[]
        current=leaf
        while current["parent_id"]:
            current=index[current["parent_id"]]
            ancestors.append(current)
        root=ancestors[-1] if ancestors else leaf
        # Plain-name query is the most direct default. Long prompt,
        # include/exclude fields and product claims are not search queries.
        candidates=[leaf["name"]]
        if root["name"].casefold() not in leaf["name"].casefold():
            candidates.append(root["name"]+" "+leaf["name"])
        for candidate in candidates:
            query=" ".join(candidate.split())
            if len(query)>105:continue
            token=query.casefold()
            if token in seen:continue
            seen.add(token)
            selected.append({"query":query,"node_id":leaf["id"],
                             "direction_id":root["id"],
                             "source_fact_ids":leaf["source_fact_ids"],
                             "research_hypothesis":leaf["name"]})
            if len(selected)>=max_queries:break
        if len(selected)>=max_queries:break
    if not selected:
        raise CollectorPlanError("No bounded queries could be generated")

    # Do not enable optional paid sort/date filters by default. The provider
    # may charge for data retrieval and other operations; no upper cost
    # bound is promised by the estimated item count.
    actor_input={"searchQueries":[x["query"] for x in selected],
                 "resultsPerPage":results_per_query,
                 "searchSection":"/video"}
    footprint={"project_id":project_id,"taxonomy_version":taxonomy_version,
               "node_ids":[n["id"] for n in published_nodes],
               "version":PLAN_VERSION,"actor_input":actor_input}
    digest=hashlib.sha256(json.dumps(footprint,sort_keys=True,ensure_ascii=False,
                                   separators=(",",":")).encode("utf-8")).hexdigest()
    return {"status":"preview_only","execution_enabled":False,
            "plan_id":digest[:24],"plan_version":PLAN_VERSION,
            "project_id":project_id,"taxonomy_version":taxonomy_version,
            "provider":"apify/clockworks~tiktok-scraper",
            "actor_input":actor_input,"query_provenance":selected,
            "requested_result_upper_bound":len(selected)*results_per_query,
            "pricing":"not_quoted","media_access":False,
            "policy_approval_required":True,
            "note":"A search preview is not a billable run; rights and budget must be separately approved."}
