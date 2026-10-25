"""B1 Stage03 deterministic, lossless technical-text inventory.

No raw PDF/website/profile text in persisted bundle. Never use A Stage10's
bounded Evidence list as a proxy for complete extracted document coverage.
No LLM, ASR, OCR, filesystem access, or network activity here.
"""
from __future__ import annotations
import hashlib
from typing import Any

from intelligence.project_sources.context_builder import sha as canonical_sha
from .contracts import A_VIDEO_SCHEMA, ContractError
from .source_routing import normalize_routing, route_source, route_profile_field

VERSION="knowledge-input-bundle/v1"
ENGINE="knowledge-inventory/1.0.0"
MAX_FRAGMENTS=50000
MAX_TEXT_CHARS=2_000_000
TEXT_SCHEMAS=frozenset(("document-segments/v1","web-source-extract/v1"))
TEXT_SOURCE_TYPES=frozenset((
    "product_website","competitor_website","research_document","research_link",
    "questionnaire_field",
))
SENSITIVE_MARKERS=("[structured value omitted; see pinned profile revision]",)


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fail(code: str):
    raise ContractError(code)


def _language(text: Any) -> str | None:
    return text if isinstance(text,str) and 2<=len(text)<=40 else None


def _fragment(bundle, *, origin, source_id, version_id, output_id, output_sha, field_path,
              profile_revision, profile_hash, segment_index, text, locator,
              role, trust, language=None):
    if not isinstance(text,str) or not text.strip():
        return None
    if len(text)>MAX_TEXT_CHARS:
        _fail("fragment_oversize")
    if not isinstance(locator,dict):
        _fail("invalid_fragment_locator")
    row={
        "origin_kind":origin,"source_id":source_id,
        "source_version_id":version_id,"output_id":output_id,
        "output_sha256":output_sha,"profile_revision":profile_revision,
        "profile_hash":profile_hash,"field_path":field_path,
        "segment_index":segment_index,"text_sha256":text_sha(text),
        "char_count":len(text),"locator":locator,
        "source_role":role.role,"subject_scope":role.scope,
        "subject_id":role.subject_id,"trust_label":trust,
        "language":language,"required":True,
    }
    row["fragment_id"]=canonical_sha({"context_sha256":bundle["context_sha256"],**row})
    return row


def _add(fragments:list, item):
    if item is not None:
        if len(fragments)>=MAX_FRAGMENTS:
            _fail("inventory_fragment_limit")
        fragments.append(item)


def _uncovered(rows: list, source_id: str | None, output_id: str | None, reason: str, *, field=None):
    rows.append({"source_id":source_id,"output_id":output_id,"field_path":field,
                 "reason":reason,"required":True})


def build_inventory(
    envelope: dict,
    outputs: dict[str,dict],
    questionnaire: dict[str,dict],
    *,
    routing_config: dict | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    """Build canonical bundle manifest; outputs/profile leaf text stay ephemeral.

    Call only inside a Stage02-verified read transaction; the repository
    verifies each input ID/SHA/provenance before using this pure builder.
    """
    ctx=envelope["context"];pid=envelope["project_id"]
    sources={s["source_id"]:s for s in ctx["source_manifest"]}
    routes=normalize_routing(routing_config,set(sources))
    config={"version":ENGINE,"source_routes":routes}
    config_sha=canonical_sha(config)
    bundle={
        "schema_version":VERSION,"inventory_version":ENGINE,
        "project_id":pid,"context_id":envelope["context_id"],
        "context_sha256":envelope["context_sha256"],
        "profile_revision":envelope["profile_revision"],
        "profile_hash":envelope["profile_hash"],
        "config_sha256":config_sha,"routing":routes,
        "approval_decision":envelope["approval"]["decision"],
    }
    fragments=[];uncovered=[];video=[];expected_output=set()
    imported_fields=set()
    for source in sorted(sources.values(),key=lambda s:s["source_id"]):
        sid=source["source_id"];vid=source["source_version_id"]
        if source["excluded_reason"] is not None:
            # A Stage11 can classify video-only analysis as no_grounded_evidence
            # although its fusion JSON is pinned. Preserve the reference and
            # the explicit exclusion; never silently convert it into B3 text.
            for oid in source["analysis_refs"]:
                output=outputs.get(oid)
                if output is not None and output.get("schema_version")==A_VIDEO_SCHEMA:
                    pin=next((x for x in ctx["analysis_refs"] if x["output_id"]==oid),None)
                    if pin is None:_fail("video_pin_not_found")
                    video.append({"source_id":sid,"source_version_id":vid,
                                  "output_id":oid,"output_sha256":pin["output_sha256"],
                                  "schema_version":A_VIDEO_SCHEMA})
            continue
        role=route_source(source,pid,routes)
        if source["source_type"]=="questionnaire_field":
            q=questionnaire.get(sid)
            if q is None:
                _uncovered(uncovered,sid,None,"questionnaire_source_missing");continue
            path=q.get("field_path");value=q.get("text")
            if not isinstance(path,str) or not path or not isinstance(value,str) or not value.strip():
                _uncovered(uncovered,sid,None,"questionnaire_field_unreadable");continue
            if text_sha(value)!=source["sha256"] or len(value.encode("utf-8"))!=source["byte_size"]:
                _uncovered(uncovered,sid,None,"questionnaire_hash_mismatch",field=path);continue
            # A profile source's operational scope is derived from pinned
            # questionnaire path, NOT from the source_kind=questionnaire_field.
            role=route_profile_field(path,pid) if sid not in routes else role
            _add(fragments,_fragment(bundle,origin="questionnaire_source",
                source_id=sid,version_id=vid,output_id=None,output_sha=None,
                profile_revision=envelope["profile_revision"],
                profile_hash=envelope["profile_hash"],field_path=path,
                segment_index=0,text=value,locator={"kind":"questionnaire","field_path":path},
                role=role,trust="user_provided_profile"))
            imported_fields.add(path)
            continue

        relevant=0
        for oid in source["analysis_refs"]:
            o=outputs.get(oid)
            if o is None:
                _fail("pinned_output_missing")
            if o.get("schema_version") not in TEXT_SCHEMAS:
                if o.get("schema_version")==A_VIDEO_SCHEMA:
                    pin=next((x for x in ctx["analysis_refs"] if x["output_id"]==oid),None)
                    if pin is None:_fail("video_pin_not_found")
                    video.append({"source_id":sid,"source_version_id":vid,
                                  "output_id":oid,"output_sha256":pin["output_sha256"],
                                  "schema_version":A_VIDEO_SCHEMA})
                continue
            relevant+=1;expected_output.add(oid)
            if o["schema_version"]=="document-segments/v1":
                segments=o.get("segments")
                if not isinstance(segments,list):
                    _uncovered(uncovered,sid,oid,"document_segments_invalid");continue
                if o.get("status")!="completed":
                    _uncovered(uncovered,sid,oid,"document_processing_incomplete")
                if not segments:
                    _uncovered(uncovered,sid,oid,"document_has_no_segments")
                for i,s in enumerate(segments):
                    if not isinstance(s,dict) or not isinstance(s.get("text"),str) or not s["text"].strip():
                        _uncovered(uncovered,sid,oid,"invalid_document_segment",field=str(i));continue
                    loc=s.get("locator")
                    if not isinstance(loc,dict):
                        _uncovered(uncovered,sid,oid,"invalid_document_locator",field=str(i));continue
                    # Original parser bounds are not necessarily normalized to
                    # the output's global char stream: preserve them verbatim.
                    if ("start_char" in loc or "end_char" in loc) and (
                        type(loc.get("start_char")) is not int or
                        type(loc.get("end_char")) is not int or
                        loc["start_char"]<0 or loc["end_char"]!=loc["start_char"]+len(s["text"])
                    ):
                        _uncovered(uncovered,sid,oid,"document_offsets_invalid",field=str(i));continue
                    try:
                        f=_fragment(bundle,origin="document_segment",source_id=sid,
                           version_id=vid,output_id=oid,
                           output_sha=next(x["output_sha256"] for x in ctx["analysis_refs"] if x["output_id"]==oid),
                           profile_revision=None,profile_hash=None,field_path=None,
                           segment_index=i,text=s["text"],locator=loc,role=role,
                           trust="local_document_extraction",language=_language(s.get("language")))
                    except ContractError:
                        _uncovered(uncovered,sid,oid,"document_fragment_oversize",field=str(i));continue
                    _add(fragments,f)
            else:
                if o.get("fetch_status") != "completed":
                    _uncovered(uncovered,sid,oid,"web_processing_incomplete")
                raw=o.get("page_text")
                if (not isinstance(raw,str) or not raw.strip()
                    or text_sha(raw)!=o.get("page_text_sha256")
                    or o.get("trust_level")!="model_reported_unverified"):
                    _uncovered(uncovered,sid,oid,"web_report_integrity");continue
                try:
                    f=_fragment(bundle,origin="web_page",source_id=sid,
                         version_id=vid,output_id=oid,
                         output_sha=next(x["output_sha256"] for x in ctx["analysis_refs"] if x["output_id"]==oid),
                         profile_revision=None,profile_hash=None,field_path=None,
                         segment_index=0,text=raw,
                         locator={"kind":"web","url":source["canonical_url"],
                                  "start_char":0,"end_char":len(raw)},
                         role=role,trust="model_reported_unverified",
                         language=_language(o.get("language")))
                except ContractError:
                    _uncovered(uncovered,sid,oid,"web_text_oversize");continue
                _add(fragments,f)
        if relevant==0 and source["source_type"] in TEXT_SOURCE_TYPES:
            _uncovered(uncovered,sid,None,"text_output_missing")

    # Stage11 stores explicitly approved, redacted profile fields. They
    # are valid pinned context text even if Stage10 profile import was never
    # run. Do not synthesize source/version UUIDs for these fields.
    fields=ctx["profile"]["fields"]
    for key in sorted(fields):
        # Links/handles referencing videos or social accounts are metadata,
        # not approved factual source text for MiMo.
        if key in ("video_references","competitor_socials","promoter_accounts"):
            continue
        if key in imported_fields:continue
        val=fields[key]
        if val is None:continue
        role=route_profile_field(key,pid)
        if isinstance(val,str):
            if val in SENSITIVE_MARKERS:
                _uncovered(uncovered,None,None,"structured_profile_omitted",field=key);continue
            if val.strip():
                _add(fragments,_fragment(bundle,origin="context_profile",
                    source_id=None,version_id=None,output_id=None,output_sha=None,
                    profile_revision=envelope["profile_revision"],
                    profile_hash=envelope["profile_hash"],field_path=key,
                    segment_index=0,text=val,locator={"kind":"context_profile","field_path":key},
                    role=role,trust="user_provided_profile"))
        elif isinstance(val,list):
            for i,item in enumerate(val):
                if not isinstance(item,str):
                    _uncovered(uncovered,None,None,"profile_item_invalid",field=f"{key}[{i}]");continue
                _add(fragments,_fragment(bundle,origin="context_profile",
                    source_id=None,version_id=None,output_id=None,output_sha=None,
                    profile_revision=envelope["profile_revision"],
                    profile_hash=envelope["profile_hash"],field_path=f"{key}[{i}]",
                    segment_index=i,text=item,
                    locator={"kind":"context_profile","field_path":f"{key}[{i}]"},
                    role=role,trust="user_provided_profile"))
        else:
            _uncovered(uncovered,None,None,"profile_field_unsupported",field=key)

    if len({x["fragment_id"] for x in fragments})!=len(fragments):
        _fail("duplicate_fragment_identity")
    fragments.sort(key=lambda x:(x["source_id"] or "",x["output_id"] or "",x["field_path"] or "",x["segment_index"],x["fragment_id"]))
    video.sort(key=lambda x:x["output_id"])
    uncovered.sort(key=lambda x:((x["source_id"] or ""),(x["output_id"] or ""),(x["field_path"] or ""),x["reason"]))
    excluded=ctx["unavailable_or_excluded_sources"]
    readiness=("blocked" if uncovered else
               "approved_partial" if excluded else
               "video_only" if not fragments and video else
               "ready" if fragments else "blocked")
    if not fragments and not video and not uncovered:
        _uncovered(uncovered,None,None,"no_eligible_content")
    bundle.update({
        "readiness":readiness,
        "fragments":fragments,
        "video_analysis_refs":video,
        "excluded_sources":excluded,
        "uncovered_required":uncovered,
        "coverage":{
            "sources_total":len(sources),
            "sources_approved_excluded":len(excluded),
            "text_outputs_indexed":len(expected_output),
            "fragments_indexed":len(fragments),
            "uncovered_required":len(uncovered),
            "video_refs":len(video),
            "profile_fragments":sum(f["origin_kind"] in ("context_profile","questionnaire_source") for f in fragments),
        },
        "text_coverage_complete":not bool(uncovered),
        "provider_calls":0,
    })
    return bundle,fragments,video


def check_fragment_text(fragment:dict, text:str) -> str:
    if not isinstance(text,str) or text_sha(text)!=fragment["text_sha256"] or len(text)!=fragment["char_count"]:
        _fail("fragment_text_integrity")
    return text
