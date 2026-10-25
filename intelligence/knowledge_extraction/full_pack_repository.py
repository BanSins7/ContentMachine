"""Stage04 B2 Full Context Pack repository: immutable, idempotent, no paid calls.

A successful Full Pack is stored as a manifest with byte-level payload SHA;
the original text is reconstructed from pinned A/Stage03 records when read.
Oversized decisions are *not* stored as pretend ready single packs; Stage05
will introduce independent multi-pack orchestration.
"""
from __future__ import annotations

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.project_sources.context_service import _assemble
from intelligence.taxonomy_builder.catalog import latest_verified
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .full_pack import MODEL_ID, normalize_config, plan_full_pack, verify_pack_payload
from .repository import KnowledgeInputRepository, _context, _verify_saved, _validate_bundle, _audit_sanitized_profile, _source_inputs
from .inventory import build_inventory, check_fragment_text
from .intake import IntakeError, _project, _uuid

def _pinned_texts(envelope, inputs, fragments):
    """Materialize without per-fragment SQL connections; never execute content."""
    outputs,questionnaires,_raw=inputs
    fields=envelope["context"]["profile"]["fields"]
    values={}
    for f in fragments:
        kind=f["origin_kind"]
        if kind=="context_profile":
            path=f["field_path"]
            key=path.split("[",1)[0]
            if key not in fields:
                raise IntakeError("pack_profile_field_missing")
            value=fields[key]
            if "[" in path:
                tail=path[len(key):]
                if not tail.startswith("[") or not tail.endswith("]") or not tail[1:-1].isdigit() or (
                    not isinstance(value,list)
                ):
                    raise IntakeError("pack_profile_field_missing")
                idx=int(tail[1:-1])
                if idx>=len(value):
                    raise IntakeError("pack_profile_field_missing")
                value=value[idx]
        elif kind=="questionnaire_source":
            q=questionnaires.get(f["source_id"])
            if not q or q["field_path"]!=f["field_path"]:
                raise IntakeError("pack_questionnaire_pin_mismatch")
            value=q["text"]
        elif kind=="document_segment":
            output=outputs.get(f["output_id"])
            if not output or output.get("schema_version")!="document-segments/v1":
                raise IntakeError("pack_document_output_missing")
            items=output.get("segments")
            idx=f["segment_index"]
            if not isinstance(items,list) or idx>=len(items) or not isinstance(items[idx],dict):
                raise IntakeError("pack_document_segment_missing")
            item=items[idx]
            if item.get("locator")!=f["locator"]:
                raise IntakeError("pack_document_locator_mismatch")
            value=item.get("text")
        elif kind=="web_page":
            output=outputs.get(f["output_id"])
            if not output or output.get("schema_version")!="web-source-extract/v1" or (
                output.get("trust_level")!="model_reported_unverified"
            ):
                raise IntakeError("pack_web_origin_invalid")
            value=output.get("page_text")
        else:
            raise IntakeError("pack_non_text_origin")
        try:
            values[f["fragment_id"]]=check_fragment_text(f,value)
        except Exception as exc:
            raise IntakeError("pack_fragment_text_integrity") from exc
    if len(values)!=len(fragments):
        raise IntakeError("pack_fragment_count_mismatch")
    return values


def _load(conn,project_id,bundle_id,resolver, *, require_current):
    row=conn.execute(
        """SELECT context_id,bundle_sha256,bundle,readiness
             FROM knowledge_input_bundles WHERE project_id=%s AND id=%s""",
        (project_id,bundle_id),
    ).fetchone()
    if row is None:
        raise IntakeError("bundle_not_found")
    cid,digest,body,readiness=row
    envelope=_context(conn,project_id,str(cid),resolver,require_current=require_current)
    if canonical_sha(body)!=digest or body["readiness"]!=readiness:
        raise IntakeError("bundle_integrity")
    _validate_bundle(body)
    _verify_saved(conn,project_id,bundle_id,body)
    return body,envelope


def _rebuild(envelope,inputs,bundle):
    outputs,questionnaire,raw=inputs
    rebuilt,fragments,_video=build_inventory(
        envelope,outputs,questionnaire,
        routing_config={"source_routes":bundle["routing"]},
    )
    _audit_sanitized_profile(envelope,raw,rebuilt)
    _validate_bundle(rebuilt)
    if canonical_sha(rebuilt)!=canonical_sha(bundle) or rebuilt!=bundle:
        raise IntakeError("bundle_reconstruction_mismatch")
    return _pinned_texts(envelope,inputs,fragments)


def _verified_catalog(conn):
    # Reuse existing immutable *observed* OpenRouter catalog snapshots.
    # No catalog refresh or network request can occur during planning.
    try:
        row=latest_verified(conn,{MODEL_ID})[MODEL_ID]
    except PipelineError as exc:
        raise IntakeError("model_catalog_not_verified") from exc
    if row.get("catalog_version_id") is None or not row.get("catalog_sha256"):
        raise IntakeError("model_catalog_not_verified")
    return row


class FullContextPackRepository:
    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.bundles=KnowledgeInputRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.bundles.connection_factory
        self.resolver=self.bundles.resolver

    def prepare(self, project_id, bundle_id, config, *, allow_partial=False):
        """Synchronous deterministic planning and optional append-only persist.

        The returned decision never includes raw text, media, secrets or model
        response. The sole "fits" record still requires provider preflight in
        Stage06+ before any paid call.
        """
        pid=_project(project_id);bid=_uuid(bundle_id,"invalid_bundle_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if conn.execute("SELECT id FROM project_registry WHERE id=%s FOR UPDATE",(pid,)).fetchone() is None:
                raise IntakeError("project_not_found")
            bundle,envelope=_load(conn,pid,bid,self.resolver,require_current=True)
            snapshot=_verified_catalog(conn)
            normalized=normalize_config(config)
            candidate,_ =plan_full_pack(bundle,None,snapshot,normalized,allow_partial=allow_partial)
            if candidate["status"] in ("blocked","requires_multi_pack"):
                return {k:v for k,v in candidate.items() if k!="pack_manifest"}
            # Only payloads potentially fitting in one provider request are
            # materialized, keeping very large inventories out of memory.
            inputs=_source_inputs(conn,pid,envelope)
            text=_rebuild(envelope,inputs,bundle)
            decision,payload=plan_full_pack(bundle,text,snapshot,normalized,allow_partial=allow_partial)
            if decision["status"]!="fits" or payload is None:
                return {k:v for k,v in decision.items() if k!="pack_manifest"}
            manifest=decision["pack_manifest"]
            digest=canonical_sha(manifest)
            if digest!=decision["pack_manifest_sha256"] or not verify_pack_payload(manifest,payload):
                raise IntakeError("full_pack_integrity")
            existing=conn.execute(
                """SELECT id,manifest_sha256,payload_sha256,manifest
                   FROM knowledge_full_context_packs
                   WHERE project_id=%s AND bundle_id=%s AND model_snapshot_id=%s
                     AND config_sha256=%s""",
                (pid,bid,snapshot["catalog_version_id"],decision["config_sha256"]),
            ).fetchone()
            if existing:
                pack_id,msha,psha,stored=existing
                if msha!=digest or psha!=decision["payload_sha256"] or stored!=manifest:
                    raise IntakeError("full_pack_idempotency_conflict")
                return {"status":"fits","pack_id":str(pack_id),"created":False,
                        "manifest_sha256":digest,"payload_sha256":psha,
                        "payload_bytes":len(payload),"fragment_count":len(manifest["entries"]),
                        "bundle_id":bid,"tokenizer_check":"pending_exact_provider_preflight",
                        "provider_calls":0}
            pack_id=conn.execute(
                """INSERT INTO knowledge_full_context_packs
                  (project_id,bundle_id,bundle_sha256,model_snapshot_id,model_id,
                   catalog_sha256,config_sha256,manifest_sha256,payload_sha256,
                   fragment_count,payload_bytes,manifest)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING id""",
                (pid,bid,candidate["bundle_sha256"],
                 snapshot["catalog_version_id"],MODEL_ID,
                 snapshot["catalog_sha256"],decision["config_sha256"],
                 digest,decision["payload_sha256"],len(manifest["entries"]),
                 len(payload),__import__("json").dumps(manifest,ensure_ascii=False)),
            ).fetchone()[0]
            return {"status":"fits","pack_id":str(pack_id),"created":True,
                    "manifest_sha256":digest,"payload_sha256":decision["payload_sha256"],
                    "payload_bytes":len(payload),"fragment_count":len(manifest["entries"]),
                    "bundle_id":bid,"tokenizer_check":"pending_exact_provider_preflight",
                    "provider_calls":0}

    def materialize(self, project_id, pack_id):
        """Historical replay with full byte/offset/identity integrity checks."""
        pid=_project(project_id);pkid=_uuid(pack_id,"invalid_pack_id")
        with self.connection_factory() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=conn.execute(
                """SELECT bundle_id,bundle_sha256,model_snapshot_id,model_id,
                    catalog_sha256,config_sha256,manifest_sha256,payload_sha256,
                    fragment_count,payload_bytes,manifest
                   FROM knowledge_full_context_packs
                   WHERE project_id=%s AND id=%s""",
                (pid,pkid),
            ).fetchone()
            if row is None:
                raise IntakeError("full_pack_not_found")
            bid,bsha,model_version,model_id,cat_hash,config_hash,mhash,psha,count,size,manifest=row
            bundle,envelope=_load(conn,pid,str(bid),self.resolver,require_current=False)
            if (canonical_sha(bundle)!=bsha or canonical_sha(manifest)!=mhash
                or manifest.get("bundle_sha256")!=bsha
                or manifest.get("config_sha256")!=config_hash
                or manifest.get("catalog_sha256")!=cat_hash
                or manifest.get("catalog_version_id")!=model_version
                or manifest.get("model_id")!=model_id
                or manifest.get("payload_sha256")!=psha
                or manifest.get("fragment_count")!=count or manifest.get("payload_bytes")!=size):
                raise IntakeError("full_pack_manifest_integrity")
            if not conn.execute(
                """SELECT 1 FROM tb_model_snapshots WHERE id=%s
                   AND model_id=%s AND catalog_sha256=%s""",
                (model_version,model_id,cat_hash),
            ).fetchone():
                raise IntakeError("model_snapshot_mismatch")
            config=normalize_config(manifest["config"])
            if canonical_sha({**config,"allow_partial":manifest["allow_partial"]})!=config_hash:
                raise IntakeError("full_pack_config_integrity")
            texts=_rebuild(envelope,_source_inputs(conn,pid,envelope),bundle)
            snapshot={
                "model_id":model_id,"catalog_version_id":model_version,
                "catalog_sha256":cat_hash,
                "capabilities":conn.execute(
                    "SELECT capabilities FROM tb_model_snapshots WHERE id=%s",
                    (model_version,),
                ).fetchone()[0]
            }
            decision,payload=plan_full_pack(
                bundle,texts,snapshot,config,allow_partial=manifest["allow_partial"])
            if (decision["status"]!="fits" or decision["pack_manifest_sha256"]!=mhash
                or decision["payload_sha256"]!=psha or payload is None
                or not verify_pack_payload(manifest,payload)):
                raise IntakeError("full_pack_replay_mismatch")
            return {"pack_id":pkid,"project_id":pid,"bundle_id":str(bid),
                    "payload_sha256":psha,"manifest_sha256":mhash,
                    "manifest":manifest,"payload":payload,
                    "provider_calls":0}
