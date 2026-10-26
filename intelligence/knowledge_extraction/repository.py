"""B Stage03 immutable PostgreSQL KnowledgeInputBundle with reproducible fragments.

No LLM or media processing; all A reads occur in the SAME transaction as
append-only persistence. Every query is scoped by project_id. Readiness is
a descriptive gate and never authorizes a paid operation.
"""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.project_sources.context_service import _snapshot, _assemble
from intelligence.project_sources.evidence_jobs import _profile_field
from .contracts import ContractError, validate_context_envelope
from .intake import PostgresAResolver, IntakeError, _project, _uuid
from .inventory import build_inventory, check_fragment_text, text_sha

@lru_cache(maxsize=1)
def bundle_validator():
    schema=json.loads((Path(__file__).parent/"schemas"/"knowledge_input_bundle_v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_bundle(bundle:dict):
    if next(bundle_validator().iter_errors(bundle),None):
        raise IntakeError("bundle_schema_invalid")
    if bundle["coverage"]["fragments_indexed"]!=len(bundle["fragments"]) or (
        bundle["coverage"]["video_refs"]!=len(bundle["video_analysis_refs"])
    ) or bundle["coverage"]["uncovered_required"]!=len(bundle["uncovered_required"]):
        raise IntakeError("bundle_coverage_mismatch")
    if bundle["text_coverage_complete"] != (not bool(bundle["uncovered_required"])):
        raise IntakeError("bundle_coverage_mismatch")


def _context(conn, pid, cid, resolver, *, require_current=True):
    # Historical replay must validate original immutable pins without
    # requiring current active source_version_id to equal a past snapshot.
    if not require_current and resolver.require_current:
        resolver=PostgresAResolver(
            resolver._factory, require_current=False,
            verify_local_files=resolver.verify_local_files)
    try:
        envelope=_snapshot(conn,pid,cid)
        validate_context_envelope(envelope,expected_project_id=pid)
        if require_current and canonical_sha(_assemble(conn,pid))!=envelope["context_sha256"]:
            raise IntakeError("stale_snapshot")
        resolver._verify_sources(conn,pid,envelope)
        resolver._verify_outputs(conn,pid,envelope)
        resolver._verify_evidence(conn,pid,envelope)
    except (IntakeError,ContractError):
        raise
    except Exception as exc:
        raise IntakeError("context_integrity") from exc
    return envelope


def _source_inputs(conn,pid,e):
    outputs={}
    for o in e["context"]["analysis_refs"]:
        row=conn.execute(
            """SELECT output,output_sha256,schema_version FROM source_processing_outputs
                 WHERE project_id=%s AND source_id=%s AND source_version_id=%s AND id=%s""",
            (pid,o["source_id"],o["source_version_id"],o["output_id"]),
        ).fetchone()
        if not row or row[1]!=o["output_sha256"] or canonical_sha(row[0])!=row[1] or row[0].get("schema_version")!=row[2]:
            raise IntakeError("output_integrity")
        outputs[o["output_id"]]=row[0]

    profile=e["context"]["profile"]
    row=conn.execute(
        """SELECT profile FROM project_profile_versions WHERE project_id=%s
             AND revision=%s AND profile_hash=%s""",
        (pid,profile["revision"],profile["hash"]),
    ).fetchone()
    if not row:raise IntakeError("profile_missing")
    raw=row[0]
    profile_sources={}
    for s in e["context"]["source_manifest"]:
        if s["excluded_reason"] is not None or s["source_type"]!="questionnaire_field":
            continue
        sid=s["source_id"];vid=s["source_version_id"]
        x=conn.execute(
            """SELECT source_locator,content_sha256,byte_size FROM project_source_versions
               WHERE project_id=%s AND source_id=%s AND id=%s""",
            (pid,sid,vid),
        ).fetchone()
        if not x:raise IntakeError("questionnaire_version_missing")
        locator,digest,size=x
        if (locator.get("kind")!="questionnaire" or locator.get("profile_revision")!=profile["revision"]
            or locator.get("profile_hash")!=profile["hash"] or not isinstance(locator.get("field_path"),str)):
            raise IntakeError("questionnaire_locator_integrity")
        try:
            value=_profile_field(raw,locator["field_path"])
        except Exception as exc:
            raise IntakeError("questionnaire_field_missing") from exc
        if text_sha(value)!=digest or len(value.encode("utf-8"))!=size:
            raise IntakeError("questionnaire_source_hash_mismatch")
        profile_sources[sid]={"field_path":locator["field_path"],"text":value}
    return outputs,profile_sources,raw


def _audit_sanitized_profile(e,raw,bundle):
    # Stage11 intentionally bounds sanitized profile lists/strings. Emit an
    # explicit blocker if it omitted source material that B cannot reconstruct
    # as an approved, individually pinned questionnaire source.
    full=raw.get("profile",raw) if isinstance(raw,dict) else {}
    if not isinstance(full,dict):return
    known={f["field_path"] for f in bundle["fragments"] if f["origin_kind"]=="questionnaire_source"}
    missing=bundle["uncovered_required"]
    for key,value in full.items():
        if key not in e["context"]["profile"]["fields"]:
            # A deliberately excludes arbitrary or sensitive fields: never
            # ingest them as silently approved extra questionnaire text.
            continue
        if isinstance(value,list) and len(value)>60 and not any(path.startswith(key+"[") for path in known):
            missing.append({"source_id":None,"output_id":None,"field_path":key,
                            "reason":"profile_list_truncated_by_a","required":True})
        if isinstance(value,str) and len(value)>12000 and key not in known:
            missing.append({"source_id":None,"output_id":None,"field_path":key,
                            "reason":"profile_text_truncated_by_a","required":True})
    if missing:
        bundle["text_coverage_complete"]=False
        bundle["readiness"]="blocked"
        bundle["coverage"]["uncovered_required"]=len(missing)
        missing.sort(key=lambda x:((x["source_id"] or ""),(x["output_id"] or ""),(x["field_path"] or ""),x["reason"]))


def _build(conn,pid,e,routing):
    outputs,profile_sources,raw=_source_inputs(conn,pid,e)
    bundle,fragments,videos=build_inventory(e,outputs,profile_sources,routing_config=routing)
    _audit_sanitized_profile(e,raw,bundle)
    _validate_bundle(bundle)
    return bundle,fragments,videos


def _verify_saved(conn,pid,bid,body):
    rows=conn.execute(
        """SELECT fragment_id,origin_kind,source_id,source_version_id,output_id,
           profile_revision,profile_hash,field_path,segment_index,text_sha256,
           char_count,locator,source_role,subject_scope,subject_id,trust_label,
           language,required
           FROM knowledge_input_fragments WHERE project_id=%s AND bundle_id=%s""",
        (pid,bid),
    ).fetchall()
    indexed={x["fragment_id"]:x for x in body["fragments"]}
    if len(rows)!=len(indexed):
        raise IntakeError("bundle_fragment_count_mismatch")
    for row in rows:
        fid,origin,sid,vid,oid,pr,ph,path,idx,digest,chars,locator,role,scope,subject,trust,lang,required=row
        entry=indexed.get(fid)
        if entry is None or (
            origin,(str(sid) if sid else None),(str(vid) if vid else None),
            (str(oid) if oid else None),pr,ph,path,idx,digest,chars,locator,
            role,scope,subject,trust,lang,required
        )!=(entry["origin_kind"],entry["source_id"],entry["source_version_id"],
             entry["output_id"],entry["profile_revision"],entry["profile_hash"],
             entry["field_path"],entry["segment_index"],entry["text_sha256"],
             entry["char_count"],entry["locator"],entry["source_role"],
             entry["subject_scope"],entry["subject_id"],entry["trust_label"],
             entry["language"],entry["required"]):
            raise IntakeError("bundle_fragment_pin_mismatch")
    videos=conn.execute(
        """SELECT source_id,source_version_id,output_id,output_sha256,schema_version
             FROM knowledge_input_video_refs WHERE project_id=%s AND bundle_id=%s""",(pid,bid)).fetchall()
    stored={(str(s),str(v),str(o),h,sv) for s,v,o,h,sv in videos}
    expected={(x["source_id"],x["source_version_id"],x["output_id"],x["output_sha256"],x["schema_version"])
              for x in body["video_analysis_refs"]}
    if stored!=expected:raise IntakeError("bundle_video_pin_mismatch")


class KnowledgeInputRepository:
    def __init__(self, connection_factory=None, *, verify_local_files=True):
        from .intake import _connect
        self.connection_factory=connection_factory or _connect
        self.resolver=PostgresAResolver(self.connection_factory,verify_local_files=verify_local_files)

    def create(self, project_id:str, context_id:str, *, source_routes:dict|None=None):
        """Idempotent append-only. No HTTP endpoint / paid call in Stage03."""
        pid=_project(project_id);cid=_uuid(context_id,"invalid_context_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            exists=conn.execute("SELECT id FROM project_registry WHERE id=%s FOR UPDATE",(pid,)).fetchone()
            if not exists:raise IntakeError("project_not_found")
            envelope=_context(conn,pid,cid,self.resolver)
            routing={"source_routes":source_routes} if source_routes is not None else None
            body,fragments,video=_build(conn,pid,envelope,routing)
            digest=canonical_sha(body)
            config_hash=body["config_sha256"]
            current=conn.execute(
                """SELECT id,bundle_sha256,bundle FROM knowledge_input_bundles
                   WHERE project_id=%s AND context_id=%s AND config_sha256=%s""",
                (pid,cid,config_hash),
            ).fetchone()
            if current:
                bid,stored_sha,existing=current
                if stored_sha!=digest or existing!=body or canonical_sha(existing)!=stored_sha:
                    raise IntakeError("bundle_idempotency_conflict")
                _verify_saved(conn,pid,bid,body)
                return {"bundle_id":str(bid),"created":False,"bundle_sha256":digest,
                        "readiness":body["readiness"],"coverage":body["coverage"],"provider_calls":0}
            bid=conn.execute(
                """INSERT INTO knowledge_input_bundles
                   (project_id,context_id,context_sha256,config_sha256,bundle_sha256,readiness,bundle)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,cid,envelope["context_sha256"],config_hash,digest,body["readiness"],Jsonb(body)),
            ).fetchone()[0]
            for f in fragments:
                conn.execute(
                    """INSERT INTO knowledge_input_fragments
                       (project_id,bundle_id,context_id,fragment_id,origin_kind,
                        source_id,source_version_id,output_id,profile_revision,profile_hash,
                        field_path,segment_index,text_sha256,char_count,locator,
                        source_role,subject_scope,subject_id,trust_label,language,required)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,bid,cid,f["fragment_id"],f["origin_kind"],f["source_id"],
                     f["source_version_id"],f["output_id"],f["profile_revision"],f["profile_hash"],
                     f["field_path"],f["segment_index"],f["text_sha256"],f["char_count"],
                     Jsonb(f["locator"]),f["source_role"],f["subject_scope"],f["subject_id"],
                     f["trust_label"],f["language"],f["required"]),
                )
            for v in video:
                conn.execute(
                    """INSERT INTO knowledge_input_video_refs
                      (project_id,bundle_id,context_id,source_id,source_version_id,output_id,
                       output_sha256,schema_version)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid,bid,cid,v["source_id"],v["source_version_id"],v["output_id"],
                     v["output_sha256"],v["schema_version"]),
                )
            return {"bundle_id":str(bid),"created":True,"bundle_sha256":digest,
                    "readiness":body["readiness"],"coverage":body["coverage"],"provider_calls":0}

    def get(self, project_id:str, bundle_id:str) -> dict:
        """Historical bundle read: always verify persisted manifest + A pins."""
        pid=_project(project_id);bid=_uuid(bundle_id,"invalid_bundle_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT context_id,context_sha256,config_sha256,bundle_sha256,readiness,bundle
                   FROM knowledge_input_bundles WHERE project_id=%s AND id=%s""",
                (pid,bid),
            ).fetchone()
            if not row:raise IntakeError("bundle_not_found")
            cid,context_sha,config_sha,digest,ready,body=row
            _context(conn,pid,str(cid),self.resolver,require_current=False)
            if (canonical_sha(body)!=digest or body["context_sha256"]!=context_sha or
                body["config_sha256"]!=config_sha or body["readiness"]!=ready
                or body["context_id"]!=str(cid)):
                raise IntakeError("bundle_integrity")
            _validate_bundle(body)
            _verify_saved(conn,pid,bid,body)
            return {"bundle_id":bid,"bundle_sha256":digest,"bundle":body}

    def resolve_fragment(self, project_id:str,bundle_id:str,fragment_id:str)->dict:
        """Reopen ORIGINAL pinned bytes/text and validate metadata+exact SHA."""
        pid=_project(project_id);bid=_uuid(bundle_id,"invalid_bundle_id")
        if not isinstance(fragment_id,str) or len(fragment_id)!=64 or any(c not in "0123456789abcdef" for c in fragment_id):
            raise IntakeError("invalid_fragment_id")
        saved=self.get(pid,bid)  # historical A SHA + all persisted rows checked
        bundle=saved["bundle"]
        fragment=next((x for x in bundle["fragments"] if x["fragment_id"]==fragment_id),None)
        if fragment is None:raise IntakeError("fragment_not_found")
        cid=bundle["context_id"]
        # This bundle already exists: immutable historical replay must remain
        # readable even after later source/profile revisions.
        with self._historical_read(pid,cid) as (conn,envelope):
            kind=fragment["origin_kind"]
            if kind=="context_profile":
                key=fragment["field_path"]
                value=envelope["context"]["profile"]["fields"]
                for token in key.split("[",1)[:1]:
                    value=value.get(token)
                if "[" in key:
                    try:value=value[int(key.split("[",1)[1].rstrip("]"))]
                    except (IndexError,ValueError,TypeError):raise IntakeError("profile_fragment_missing")
                text=value
            elif kind=="questionnaire_source":
                row=conn.execute(
                    """SELECT profile FROM project_profile_versions WHERE project_id=%s
                       AND revision=%s AND profile_hash=%s""",
                    (pid,fragment["profile_revision"],fragment["profile_hash"]),
                ).fetchone()
                if not row:raise IntakeError("profile_fragment_missing")
                try:text=_profile_field(row[0],fragment["field_path"])
                except Exception as exc:raise IntakeError("profile_fragment_missing") from exc
            else:
                result=conn.execute(
                    """SELECT output,output_sha256 FROM source_processing_outputs
                       WHERE project_id=%s AND id=%s AND source_id=%s
                       AND source_version_id=%s""",
                    (pid,fragment["output_id"],fragment["source_id"],fragment["source_version_id"]),
                ).fetchone()
                if not result or result[1]!=fragment["output_sha256"] or canonical_sha(result[0])!=result[1]:
                    raise IntakeError("fragment_origin_integrity")
                if kind=="web_page":
                    text=result[0].get("page_text")
                else:
                    segments=result[0].get("segments")
                    i=fragment["segment_index"]
                    if not isinstance(segments,list) or i>=len(segments):
                        raise IntakeError("fragment_origin_missing")
                    item=segments[i]
                    if not isinstance(item,dict) or item.get("locator")!=fragment["locator"]:
                        raise IntakeError("fragment_locator_integrity")
                    text=item.get("text")
            try:check_fragment_text(fragment,text)
            except ContractError as exc:raise IntakeError("fragment_text_integrity") from exc
            return {"fragment_id":fragment_id,"text":text,"locator":fragment["locator"],
                    "source_role":fragment["source_role"],"subject_id":fragment["subject_id"],
                    "trust_label":fragment["trust_label"],"source_id":fragment["source_id"],
                    "source_version_id":fragment["source_version_id"]}

    def _historical_read(self,pid,cid):
        # Historical exact pinned replay, *never* for creating a NEW B run.
        from contextlib import contextmanager
        @contextmanager
        def _read():
            with self.connection_factory() as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                e=_context(conn,pid,cid,self.resolver,require_current=False)
                yield conn,e
        return _read()
