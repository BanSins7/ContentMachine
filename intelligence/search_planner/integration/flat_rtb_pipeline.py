"""Verified, immutable flat RTB V1 publication -> genuine SP contexts.

Flat topics remain flat. The fixed technical group used for SP batching is
not claimed to be a research direction, Deep C9 or an inferred hierarchy.
No provider calls, paid actions, review mutations or fabricated facts.
"""
from __future__ import annotations

from copy import deepcopy

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid, _connect
from intelligence.search_planner.contracts import publication_uuid, validate_search_policy
from intelligence.search_planner.context_builder import _project_context

SOURCE_FLAT = "rtb_flat_v1"
_TECH_GROUP = "flat-rtb-v1"
_REQUIRED = frozenset(("id", "name", "definition", "include", "exclude",
    "fact_ids", "positive_examples", "negative_examples", "relation", "origin",
    "review_status"))

def verify_flat_snapshot(snapshot, project_id, publication_id):
    """Defensive validation independent of source SQL / HTTP response data."""
    pid, pub = _pid(project_id), publication_uuid(publication_id)
    if not isinstance(snapshot, dict) or snapshot.get("project_id") != pid or (
        snapshot.get("taxonomy_publication_id") != pub):
        raise PipelineError("Foreign or missing flat RTB publication", 409)
    publication_uuid(snapshot.get("rtb_run_id"))
    version = snapshot.get("taxonomy_version")
    if type(version) is not int or version <= 0 or (
        snapshot.get("run_status") != "approved"
        or snapshot.get("sre_status") != "approved"
        or snapshot.get("approved_by") != "local_owner"):
        raise PipelineError("Flat publication lacks final owner and SRE approval", 409)
    facts = snapshot.get("approved_facts")
    topics = snapshot.get("topics")
    profile = snapshot.get("profile_snapshot")
    if (not isinstance(facts, list) or not facts or
        not isinstance(topics, list) or not 1 <= len(topics) <= 20 or
        not isinstance(profile, dict)):
        raise PipelineError("Flat RTB publication is missing approved content", 409)
    known = {}
    for fact in facts:
        if (not isinstance(fact, dict) or not isinstance(fact.get("id"), str)
            or not fact["id"] or fact["id"] in known
            or not isinstance(fact.get("text"), str) or not fact["text"].strip()
            or fact.get("kind") not in ("function","problem","audience","product_type")):
            raise PipelineError("Invalid approved fact in flat publication", 409)
        known[fact["id"]] = fact
    ids, names = set(), set()
    for t in topics:
        if not isinstance(t, dict) or not _REQUIRED.issubset(t):
            raise PipelineError("Incomplete approved flat topic", 409)
        topic_id = publication_uuid(t["id"])
        name = t["name"]
        if not isinstance(name,str) or not name.strip() or len(name)>160:
            raise PipelineError("Invalid approved flat topic name",409)
        if topic_id in ids or name.casefold() in names:
            raise PipelineError("Duplicate approved flat topic", 409)
        ids.add(topic_id); names.add(name.casefold())
        for key in ("definition","include","exclude"):
            if not isinstance(t[key],str) or not t[key].strip():
                raise PipelineError("Incomplete flat topic research boundary",409)
        for key in ("positive_examples","negative_examples"):
            if (not isinstance(t[key],list) or not t[key] or
                any(not isinstance(x,str) or not x.strip() for x in t[key])):
                raise PipelineError("Invalid flat topic example",409)
        fact_ids = t["fact_ids"]
        if (not isinstance(fact_ids,list) or len(fact_ids)!=len(set(fact_ids))
            or any(fid not in known for fid in fact_ids)):
            raise PipelineError("Flat topic cites an unapproved fact",409)
        if t["relation"] not in ("direct","problem_related","adjacent") or (
           t["relation"]!="adjacent" and not fact_ids):
            raise PipelineError("Invalid flat topic grounding relation",409)
        if t["review_status"]!="approved":
            raise PipelineError("Flat topic not finally approved",409)
    digest=sha256(topics)
    if (snapshot.get("taxonomy_sha256")!=digest or
        snapshot.get("sre_snapshot_sha256")!=digest):
        raise PipelineError("Flat RTB publication/SRE checksum mismatch",409)
    return deepcopy(snapshot)


def read_flat_publication(conn, project_id, publication_id):
    pid, pub = _pid(project_id), publication_uuid(publication_id)
    row=conn.execute("""SELECT p.run_id,p.taxonomy_version,p.approved_topics,
       r.profile_snapshot,f.approved_facts,l.source_sha256,
       v.status,v.snapshot_checksum,p.approved_by
       FROM tb_taxonomy_publications p
       JOIN tb_runs r ON r.id=p.run_id AND r.project_id=p.project_id
                   AND r.status='approved'
       JOIN tb_fact_review_submissions f ON f.run_id=p.run_id
                                      AND f.project_id=p.project_id
       JOIN tb_sre_taxonomy_links l ON l.builder_publication_id=p.id
           AND l.run_id=p.run_id AND l.project_id=p.project_id
       JOIN sre_taxonomy_versions v ON v.id=l.sre_taxonomy_version_id
                                  AND v.project_id=p.project_id
       WHERE p.project_id=%s AND p.id=%s::uuid AND p.approved_by='local_owner'
         AND f.reviewed_by='local_owner' AND v.status='approved'
       """,(pid,pub)).fetchone()
    if row is None:raise PipelineError("Published/approved flat RTB source not found",404)
    snapshot={
      "project_id":pid,"taxonomy_publication_id":pub,
      "rtb_run_id":str(row[0]),"taxonomy_version":row[1],
      "topics":row[2],"profile_snapshot":row[3],"approved_facts":row[4],
      "taxonomy_sha256":row[5],"sre_snapshot_sha256":row[7],
      "run_status":"approved","sre_status":row[6],"approved_by":row[8],
      "publication_source":SOURCE_FLAT,
    }
    return verify_flat_snapshot(snapshot,pid,pub)


def load_flat_publication(project_id, publication_id, *, connect=None):
    factory=connect or _connect
    with factory() as conn:
        return read_flat_publication(conn,project_id,publication_id)


def list_flat_publications(project_id, *, limit=30, connect=None):
    pid=_pid(project_id)
    if type(limit) is not int or not 1<=limit<=50:
        raise PipelineError("Invalid flat publication page size",422)
    factory=connect or _connect
    with factory() as conn:
        rows=conn.execute("""SELECT p.id,p.approved_at
             FROM tb_taxonomy_publications p
             JOIN tb_runs r ON r.id=p.run_id AND r.project_id=p.project_id
                AND r.status='approved'
             JOIN tb_fact_review_submissions f ON f.run_id=p.run_id
                AND f.project_id=p.project_id
             JOIN tb_sre_taxonomy_links l ON l.builder_publication_id=p.id
                AND l.run_id=p.run_id AND l.project_id=p.project_id
             JOIN sre_taxonomy_versions v ON v.id=l.sre_taxonomy_version_id
                AND v.project_id=p.project_id AND v.status='approved'
             WHERE p.project_id=%s AND p.approved_by='local_owner'
               AND f.reviewed_by='local_owner'
             ORDER BY p.approved_at DESC,p.id DESC LIMIT %s""",
             (pid,limit)).fetchall()
        items=[]
        for pubid,created in rows:
            snap=read_flat_publication(conn,pid,str(pubid))
            items.append({
              "taxonomy_publication_id":str(pubid),
              "taxonomy_version":snap["taxonomy_version"],
              "taxonomy_sha256":snap["taxonomy_sha256"],
              "approved_hypothesis_count":len(snap["topics"]),
              "approved_at":created.isoformat() if created else None,
              "source_status":"published_reviewed_flat_rtb",
              "publication_source":SOURCE_FLAT,
            })
    return {"schema_version":"search-planner-approved-flat-publications/v1",
            "project_id":pid,"publications":items,"count":len(items)}


def build_flat_contexts(source, search_policy):
    """One complete SP context per actual approved flat topic; no fake Deep nodes."""
    policy=validate_search_policy(search_policy)
    snap=verify_flat_snapshot(source,source.get("project_id"),source.get("taxonomy_publication_id"))
    facts={fact["id"]:fact for fact in snap["approved_facts"]}
    topics=snap["topics"]
    technical_group={
      "id":_TECH_GROUP,"name":"Техническая группа плоских тем RTB",
      "kind":"flat_group","source_kind":"not_a_deep_research_direction",
    }
    contexts=[]
    for topic in sorted(topics,key=lambda x:x["id"]):
        fid=topic["fact_ids"]
        other=[{"id":other["id"],"name":other["name"],
           "definition":other["definition"],"include":other["include"],
           "exclude":other["exclude"]}
           for other in sorted(topics,key=lambda x:x["id"])
           if other["id"]!=topic["id"]][:5]
        context={
          "context_schema_version":"hypothesis-context/flat-v1",
          "publication_source":SOURCE_FLAT,
          "project_id":snap["project_id"],"rtb_run_id":snap["rtb_run_id"],
          "taxonomy_publication_id":snap["taxonomy_publication_id"],
          "taxonomy_version":snap["taxonomy_version"],
          "taxonomy_sha256":snap["taxonomy_sha256"],
          "ancestry_path":[_TECH_GROUP,topic["id"]],
          "direction":deepcopy(technical_group),"branch":None,
          "hypothesis":deepcopy(topic),
          "approved_facts":[{
             "id":facts[ref]["id"],"kind":facts[ref]["kind"],
             "text":facts[ref]["text"],"source_quote":facts[ref].get("source_quote"),
             "origin":facts[ref].get("origin")} for ref in sorted(fid)],
          "project_context":_project_context(snap["profile_snapshot"]),
          "neighbor_hypotheses":other,
          "search_policy":deepcopy(policy),
          "derived_search_facets":[],
          "flat_rtb_provenance":{
             "source_kind":"human_approved_flat_taxonomy_not_deep_c9",
             "topic":deepcopy(topic),
             "approved_fact_ids":sorted(fid),
             "taxonomy_sha256":snap["taxonomy_sha256"],
             "sre_snapshot_sha256":snap["sre_snapshot_sha256"],
          }
        }
        contexts.append(context)
    return {"project_id":snap["project_id"],
       "rtb_run_id":snap["rtb_run_id"],
       "taxonomy_publication_id":snap["taxonomy_publication_id"],
       "taxonomy_version":snap["taxonomy_version"],
       "taxonomy_sha256":snap["taxonomy_sha256"],
       "publication_source":SOURCE_FLAT},contexts
