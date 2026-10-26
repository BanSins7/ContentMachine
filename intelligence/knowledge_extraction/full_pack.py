"""B2 Stage04: deterministic SINGLE full-context pack, with zero provider calls.

No splitting, loss, editing, summarizing, prompt execution or budget consent.
This is a conservative *planning* gate: B3 must repeat preflight with the
ACTUAL provider messages, schema, model and tokenizer before dispatch.
"""
from __future__ import annotations

import hashlib
import json

from intelligence.project_sources.context_builder import sha as canonical_sha
from .intake import IntakeError
from .inventory import check_fragment_text

PACK_VERSION = "evidence-pack/v1"
PLANNER_VERSION = "ke-full-pack/1.0.0"
MODEL_ID = "xiaomi/mimo-v2.6-flash"
MAX_PACK_BYTES = 8_000_000
MAX_FRAGMENTS = 50_000
WIRE_OVERHEAD = 2048
BYTE_MULTIPLIER = 2  # mirrors taxonomy_builder.provider.reservation_cost

def _json(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def _hash_bytes(blob):
    return hashlib.sha256(blob).hexdigest()

def normalize_config(raw):
    if not isinstance(raw, dict) or set(raw) != {
        "max_output_tokens", "instruction_reserve_tokens",
        "schema_reserve_tokens", "safety_margin_tokens",
    }:
        raise IntakeError("full_pack_config_invalid")
    limits = {
        "max_output_tokens": (128, 100_000),
        "instruction_reserve_tokens": (512, 100_000),
        "schema_reserve_tokens": (512, 100_000),
        "safety_margin_tokens": (1024, 100_000),
    }
    config = {}
    for key, (minimum, maximum) in limits.items():
        value = raw[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise IntakeError("full_pack_config_invalid")
        config[key] = value
    return config


def normalize_snapshot(snapshot):
    """Pinned exact observed catalog snapshot; never infer unlimited context."""
    if not isinstance(snapshot, dict):
        raise IntakeError("model_snapshot_invalid")
    ident, version = snapshot.get("model_id"), snapshot.get("catalog_version_id")
    digest, cap = snapshot.get("catalog_sha256"), snapshot.get("capabilities")
    if ident != MODEL_ID or type(version) is not int or version < 1:
        raise IntakeError("model_snapshot_invalid")
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise IntakeError("model_snapshot_invalid")
    if not isinstance(cap, dict) or not cap.get("text_output") or not cap.get("supports_structured_outputs"):
        raise IntakeError("model_capability_unsupported")
    context = cap.get("context_length")
    if type(context) is not int or not 2048 <= context <= 5_000_000:
        raise IntakeError("model_context_unknown")
    maximum = cap.get("max_output_tokens")
    if maximum is not None and (type(maximum) is not int or maximum < 1):
        raise IntakeError("model_context_unknown")
    return {"model_id":ident,"catalog_version_id":version,
            "catalog_sha256":digest,"context_window_tokens":context,
            "max_provider_output_tokens":maximum}


def _header(bundle):
    return {"schema_version":PACK_VERSION,
            "project_id":bundle["project_id"],"context_id":bundle["context_id"],
            "context_sha256":bundle["context_sha256"],
            "bundle_sha256":canonical_sha(bundle),
            "policy":"pinned-technical-text-only/no-model-verified-truth",
            "kind":"header"}


def _line(fragment, text):
    # This payload MUST NOT contain video, transcript, URL-derived handles,
    # or model-supplied commands from other sources as executable instructions.
    return {"kind":"fragment","fragment_id":fragment["fragment_id"],
            "origin_kind":fragment["origin_kind"],"text":text,
            "source_id":fragment["source_id"],
            "source_version_id":fragment["source_version_id"],
            "output_id":fragment["output_id"],
            "field_path":fragment["field_path"],
            "segment_index":fragment["segment_index"],
            "locator":fragment["locator"],
            "source_role":fragment["source_role"],
            "subject_scope":fragment["subject_scope"],
            "subject_id":fragment["subject_id"],
            "trust_label":fragment["trust_label"],
            "exact_text_sha256":fragment["text_sha256"],
            "evidence_ids":fragment["evidence_ids"]}


def _decision(bundle, model, config, *, allow_partial):
    if bundle.get("schema_version") != "knowledge-input-bundle/v1":
        raise IntakeError("bundle_schema_invalid")
    result = {"schema_version":"full-context-decision/v1",
              "project_id":bundle["project_id"],
              "context_id":bundle["context_id"],
              "bundle_sha256":canonical_sha(bundle),
              "model_id":model["model_id"],
              "catalog_version_id":model["catalog_version_id"],
              "catalog_sha256":model["catalog_sha256"],
              "config":config,"config_sha256":canonical_sha({**config,"allow_partial":allow_partial}),
              "provider_calls":0,"tokenizer_check":"pending_exact_provider_preflight",
              "planner_version":PLANNER_VERSION}
    if bundle["readiness"] == "blocked" or bundle.get("uncovered_required"):
        result.update(status="blocked",reason="uncovered_required",pack_count=0)
    elif bundle["readiness"] == "video_only" or not bundle["fragments"]:
        result.update(status="blocked",reason="no_text_fragments",pack_count=0)
    elif bundle["readiness"] == "approved_partial" and not allow_partial:
        result.update(status="blocked",reason="partial_requires_explicit_acknowledgment",pack_count=0)
    else:
        result.update(status=None,reason=None,pack_count=0)
    result["partial_acknowledged"]=allow_partial
    result["excluded_sources_count"]=len(bundle["excluded_sources"])
    result["video_refs_excluded_count"]=len(bundle["video_analysis_refs"])
    return result


def plan_full_pack(bundle, text_by_id, model_snapshot, raw_config, *, allow_partial=False):
    """Pure: all-or-nothing. Return decision and optional exact byte payload.

    No need to allocate oversize raw text: caller may supply no texts to
    classify known-too-large inventory for the Stage05 multi-pack planner.
    """
    model=normalize_snapshot(model_snapshot)
    config=normalize_config(raw_config)
    if type(allow_partial) is not bool:
        raise IntakeError("partial_acknowledgment_invalid")
    decision=_decision(bundle,model,config,allow_partial=allow_partial)
    if decision["status"]=="blocked":
        return decision, None
    fragments=bundle["fragments"]
    if not isinstance(fragments,list) or len(fragments)>MAX_FRAGMENTS or (
        len({f["fragment_id"] for f in fragments})!=len(fragments)
    ):
        raise IntakeError("fragment_manifest_integrity")

    reserved=sum(config.values()) + WIRE_OVERHEAD
    available=model["context_window_tokens"]-reserved
    if (model["max_provider_output_tokens"] is not None
        and config["max_output_tokens"] > model["max_provider_output_tokens"]):
        decision.update(status="blocked",reason="model_output_limit",pack_count=0)
        return decision,None
    decision.update(context_window_tokens=model["context_window_tokens"],
                    reserve_tokens=reserved,available_input_tokens=max(0,available),
                    text_fragments_required=len(fragments))
    if available <= 0:
        decision.update(status="requires_multi_pack",reason="window_reserved_capacity",
                        pack_count=0)
        return decision,None

    # Minimum UTF-8 bytes >= Unicode scalar character count. If this alone
    # breaches context headroom, do not materialize a gigantic payload.
    min_bytes=sum(f["char_count"] for f in fragments)
    if min_bytes > MAX_PACK_BYTES or BYTE_MULTIPLIER * min_bytes > available:
        decision.update(status="requires_multi_pack",reason="lower_bound_exceeds_single_window",
                        minimum_source_characters=min_bytes,pack_count=0)
        return decision,None

    # Metadata-only probe: B2 repo can avoid loading text that clearly will
    # not fit, even when the context is a very large document.
    if text_by_id is None:
        decision.update(status="candidate",reason="requires_exact_payload_measurement",
                        pack_count=0,minimum_source_characters=min_bytes)
        return decision,None
    if not isinstance(text_by_id,dict) or set(text_by_id)!={x["fragment_id"] for x in fragments}:
        raise IntakeError("full_pack_text_coverage_mismatch")
    header=(_json(_header(bundle))+"\n").encode("utf-8")
    wire=[header]; entries=[];cursor=len(header);ids=set()
    for fragment in fragments:
        if fragment["origin_kind"] not in ("context_profile","questionnaire_source","document_segment","web_page"):
            raise IntakeError("non_text_fragment_in_pack")
        fid=fragment["fragment_id"]
        if fid in ids:raise IntakeError("duplicate_fragment_identity")
        ids.add(fid)
        text=check_fragment_text(fragment,text_by_id[fid])
        encoded=(_json(_line(fragment,text))+"\n").encode("utf-8")
        row={"fragment_id":fid,"text_sha256":fragment["text_sha256"],
             "line_sha256":_hash_bytes(encoded),
             "byte_start":cursor,"byte_end":cursor+len(encoded),
             "source_id":fragment["source_id"],
             "source_version_id":fragment["source_version_id"],
             "output_id":fragment["output_id"],
             "source_role":fragment["source_role"],
             "subject_id":fragment["subject_id"]}
        cursor+=len(encoded);entries.append(row);wire.append(encoded)
        if cursor>MAX_PACK_BYTES or BYTE_MULTIPLIER*cursor+WIRE_OVERHEAD>available:
            decision.update(status="requires_multi_pack",
                            reason="encoded_payload_exceeds_single_window",
                            measured_prefix_bytes=cursor,pack_count=0)
            return decision,None

    payload=b"".join(wire)
    measured_input=BYTE_MULTIPLIER*len(payload)+WIRE_OVERHEAD
    if measured_input>available:raise IntakeError("pack_capacity_internal_error")
    decision.update(status="fits",reason=None,pack_count=1,
                    payload_sha256=_hash_bytes(payload),
                    payload_bytes=len(payload),estimated_input_tokens=measured_input,
                    fragment_ids=[f["fragment_id"] for f in fragments],
                    segment_count=len(entries))
    manifest={"schema_version":PACK_VERSION,"planner_version":PLANNER_VERSION,
              "project_id":bundle["project_id"],"context_id":bundle["context_id"],
              "context_sha256":bundle["context_sha256"],
              "bundle_sha256":canonical_sha(bundle),
              "model_id":model["model_id"],"catalog_version_id":model["catalog_version_id"],
              "catalog_sha256":model["catalog_sha256"],
              "config":config,"config_sha256":decision["config_sha256"],
              "allow_partial":allow_partial,
              "token_estimation":"utf8_wire_bytes_x2_plus_2048",
              "tokenizer_check":"pending_exact_provider_preflight",
              "header_sha256":_hash_bytes(header),
              "entries":entries,"fragment_count":len(entries),
              "payload_sha256":decision["payload_sha256"],
              "payload_bytes":len(payload),"estimated_input_tokens":measured_input,
              "context_window_tokens":model["context_window_tokens"],
              "reserve_tokens":reserved,
              "excluded_source_count":len(bundle["excluded_sources"]),
              "video_refs_excluded_count":len(bundle["video_analysis_refs"])}
    decision["pack_manifest"]=manifest
    decision["pack_manifest_sha256"]=canonical_sha(manifest)
    return decision,payload


def verify_pack_payload(manifest,payload):
    """Deterministic replay: strict byte offsets, line hashes and identity."""
    if not isinstance(payload,bytes) or not isinstance(manifest,dict):
        raise IntakeError("pack_payload_invalid")
    if len(payload)!=manifest.get("payload_bytes") or _hash_bytes(payload)!=manifest.get("payload_sha256"):
        raise IntakeError("pack_payload_integrity")
    entries=manifest.get("entries")
    if not isinstance(entries,list) or len(entries)!=manifest.get("fragment_count"):
        raise IntakeError("pack_manifest_integrity")
    first=payload.find(b"\n")+1
    if first<=0 or _hash_bytes(payload[:first])!=manifest.get("header_sha256"):
        raise IntakeError("pack_header_integrity")
    try:
        header=json.loads(payload[:first])
    except (UnicodeDecodeError,ValueError) as exc:
        raise IntakeError("pack_header_integrity") from exc
    if header.get("schema_version")!=PACK_VERSION or header.get("bundle_sha256")!=manifest.get("bundle_sha256"):
        raise IntakeError("pack_header_integrity")
    cursor=first;seen=set()
    for row in entries:
        start,end=row.get("byte_start"),row.get("byte_end")
        if type(start) is not int or type(end) is not int or start!=cursor or end<=start or end>len(payload):
            raise IntakeError("pack_offset_integrity")
        line=payload[start:end]
        if not line.endswith(b"\n") or _hash_bytes(line)!=row.get("line_sha256"):
            raise IntakeError("pack_line_integrity")
        try:
            parsed=json.loads(line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise IntakeError("pack_line_integrity") from exc
        fid=row.get("fragment_id")
        if fid in seen or parsed.get("fragment_id")!=fid or parsed.get("source_id")!=row.get("source_id"):
            raise IntakeError("pack_fragment_identity")
        content=parsed.get("text")
        if not isinstance(content,str) or hashlib.sha256(content.encode("utf-8")).hexdigest()!=row.get("text_sha256"):
            raise IntakeError("pack_fragment_integrity")
        if parsed.get("source_role")!=row.get("source_role") or parsed.get("subject_id")!=row.get("subject_id"):
            raise IntakeError("pack_source_role_integrity")
        cursor=end;seen.add(fid)
    if cursor!=len(payload):
        raise IntakeError("pack_coverage_integrity")
    return True
