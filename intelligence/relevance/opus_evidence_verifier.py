"""M21: strict Opus JSON, source evidence and full-chunk aggregation gate.

NO provider calls, raw answer fabrication, retries, spending or DB writes.
The pure boundary validates transport structure, NOT a source authorization,
provider identity/receipt or whether the semantic judgment is actually true.
The trusted entry re-derives the exact M20 manifest from live PostgreSQL
C9/D1 and operator-pinned catalog before accepting any answer.
"""
from __future__ import annotations

import json

from .opus_request_packer import (
    PACK_VERSION, PER_REQUEST_OVERHEAD, SYSTEM_INSTRUCTION,
    _check_lossless, _size, pack_live_opus_requests,
)
from .v1_contracts import (
    canonical_json, canonical_uuid, fingerprint, positive, project_id,
    record, reject, sha256_digest,
)

VERSION = "sre-opus-evidence-verifier/v1"
DECISIONS = frozenset(("match", "no_match", "uncertain"))
MAX_ANSWER_UTF8 = 32768
MAX_REASON_CHARS = 4096
MAX_EVIDENCE_PER_CHUNK = 16
MAX_RESPONSES = 2048
_EVIDENCE = (
    "source", "index", "start_char", "end_char", "quote", "start_ms", "end_ms",
)
_REPLY = ("category_key", "decision", "reason", "evidence")


def _parse_raw(raw):
    if type(raw) is not str or not raw:
        reject("opus.reply", "missing or oversized provider JSON",
               "malformed_model_output")
    try:
        size=len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        reject("opus.reply", "invalid Unicode model JSON",
               "malformed_model_output")
    if size>MAX_ANSWER_UTF8:
        reject("opus.reply", "missing or oversized provider JSON",
               "malformed_model_output")

    def strict_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                reject("opus.reply", "duplicate JSON field",
                       "malformed_model_output")
            result[key]=value
        return result

    def invalid_constant(_):
        reject("opus.reply", "non-finite JSON number",
               "malformed_model_output")
    try:
        answer=json.loads(raw,object_pairs_hook=strict_pairs,
                          parse_constant=invalid_constant)
    except (ValueError,UnicodeError,RecursionError):
        reject("opus.reply", "invalid JSON (no markdown/fences/remainder)",
               "malformed_model_output")
    return answer


def _request_payload(request):
    record(request,"opus.request",required=(
        "schema_version","request_ordinal","category_key","chunk_index",
        "chunk_count","original_packet_sha256","system_sha256","model_id",
        "model_revision","catalog_revision","catalog_sha256",
        "max_output_tokens","temperature_milli","messages",
        "input_token_proxy_units","token_count_exact",
        "requires_provider_preflight","dispatch_allowed","paid_calls",
        "request_sha256",
    ))
    if (request["schema_version"]!=PACK_VERSION or
            request["dispatch_allowed"] is not False or
            request["paid_calls"]!=0 or
            request["token_count_exact"] is not False or
            request["requires_provider_preflight"] is not True or
            request["temperature_milli"]!=0 or
            request["request_sha256"]!=fingerprint({
                k:v for k,v in request.items() if k!="request_sha256"
            })):
        reject("opus.request","unsealed or dispatchable request",
               "snapshot_mismatch")
    messages=request["messages"]
    if (type(messages) is not list or len(messages)!=2 or
            messages[0]!={"role":"system","content":SYSTEM_INSTRUCTION} or
            type(messages[1]) is not dict or
            set(messages[1])!={"role","content"} or
            messages[1].get("role")!="user" or
            type(messages[1].get("content")) is not str):
        reject("opus.request","role/instruction spoofing","snapshot_mismatch")
    try:
        payload=json.loads(messages[1]["content"])
    except (TypeError,ValueError):
        reject("opus.request","unparseable payload","snapshot_mismatch")
    if messages[1]["content"]!=canonical_json(payload):
        reject("opus.request","noncanonical response input","snapshot_mismatch")
    record(payload,"opus.request_payload",required=(
        "schema_version","task","expected_category_key",
        "source_pins","approved_taxonomy_data","observation_header",
        "chunk","trust_boundary","output_instruction",
    ))
    if (payload["schema_version"]!=PACK_VERSION or
            payload["expected_category_key"]!=request["category_key"] or
            payload["task"]!="one_category_one_observation_slice" or
            type(payload["source_pins"]) is not dict or
            type(payload["approved_taxonomy_data"]) is not dict or
            payload["approved_taxonomy_data"].get("category_key")!=
            request["category_key"] or
            type(payload["trust_boundary"]) is not dict or
            payload["trust_boundary"].get("data_only") is not True or
            payload["trust_boundary"].get("external_llm_send_authorized") is not False):
        reject("opus.request","source, category or trust boundary drift",
               "snapshot_mismatch")
    chunk=payload["chunk"]
    record(chunk,"opus.chunk",required=("index","count","parts"))
    if (chunk["index"]!=request["chunk_index"] or
            chunk["count"]!=request["chunk_count"] or
            type(chunk["parts"]) is not list):
        reject("opus.chunk","chunk coverage does not match plan",
               "incomplete_coverage")
    if (request["input_token_proxy_units"]!=_size(payload) or
            request["input_token_proxy_units"]<=0 or
            type(request["input_token_proxy_units"]) is not int):
        reject("opus.request","input proxy tampering","snapshot_mismatch")
    return payload


def _manifest(m20):
    """Shape-level consistent M20 plan; not proof of DB or provider origin."""
    if type(m20) is not dict or m20.get("schema_version")!=PACK_VERSION:
        reject("opus.plan","invalid request plan schema","snapshot_mismatch")
    if m20.get("batch_manifest_sha256")!=fingerprint({
        k:v for k,v in m20.items() if k!="batch_manifest_sha256"
    }):
        reject("opus.plan","batch manifest integrity mismatch","snapshot_mismatch")
    if (m20.get("dispatch_allowed") is not False or
            m20.get("paid_calls")!=0 or
            m20.get("token_count_exact") is not False or
            m20.get("requires_provider_preflight") is not True or
            m20.get("dropped_count")!=0):
        reject("opus.plan","request plan cannot claim paid approval",
               "rights_denied")
    keys=m20.get("expected_hypothesis_keys")
    reqs=m20.get("requests")
    batches=m20.get("batches")
    coverage=m20.get("hypothesis_coverage")
    if (type(keys) is not list or not 1<=len(keys)<=48 or
            keys!=sorted(set(keys)) or
            type(reqs) is not list or not 1<=len(reqs)<=MAX_RESPONSES or
            type(batches) is not list or not batches or
            type(coverage) is not list or len(coverage)!=len(keys) or
            m20.get("hypothesis_count")!=len(keys) or
            m20.get("request_count")!=len(reqs) or
            m20.get("batch_count")!=len(batches)):
        reject("opus.plan","missing/duplicate C9 hypotheses or requests",
               "incomplete_coverage")
    grouped={key:[] for key in keys}
    for i,r in enumerate(reqs):
        body=_request_payload(r)
        key=canonical_uuid(r["category_key"],"opus.category_key")
        if (key not in grouped or r["request_ordinal"]!=i or
                r["model_id"]!=m20.get("model_id") or
                r["model_revision"]!=m20.get("model_revision") or
                r["catalog_revision"]!=m20.get("catalog_revision") or
                r["catalog_sha256"]!=m20.get("catalog_sha256") or
                r["input_token_proxy_units"]>m20.get("input_token_budget_proxy",0)):
            reject("opus.plan","model/category/order or size drift",
                   "snapshot_mismatch")
        pins=body["source_pins"]
        if (pins.get("project_id")!=m20.get("project_id") or
                pins.get("publication_id")!=m20.get("publication_id") or
                pins.get("content_version_id")!=m20.get("content_version_id") or
                pins.get("content_sha256")!=m20.get("content_sha256")):
            reject("opus.plan","D1 source-version drift","snapshot_mismatch")
        grouped[key].append((r,body))
    if [x["category_key"] for x in coverage]!=keys:
        reject("opus.plan","C9 coverage map altered","incomplete_coverage")
    for item in coverage:
        key=item["category_key"]
        entries=grouped[key]
        if (not entries or item["chunk_count"]!=len(entries) or
                any(r["chunk_index"]!=i or r["chunk_count"]!=len(entries)
                    for i,(r,_) in enumerate(entries)) or
                any(r["original_packet_sha256"]!=item["packet_sha256"]
                    for r,_ in entries) or
                any(b["observation_header"].get(
                    "original_observations_sha256")!=item[
                    "source_observations_sha256"]
                    for _,b in entries)):
            reject("opus.coverage","missing/reordered source slices",
                   "incomplete_coverage")
        first=entries[0][1]
        if any(b["approved_taxonomy_data"]!=first["approved_taxonomy_data"] or
               b["source_pins"]!=first["source_pins"] or
               b["observation_header"]!=first["observation_header"]
               for _,b in entries):
            reject("opus.coverage","approved C9/context source changed across slices",
                   "snapshot_mismatch")
        # The whole D1 text can be reconstructed from the slices, and checked
        # against a genuine DB-backed M20 source in verify_live_opus_responses.
        for _,b in entries:
            for part in b["chunk"]["parts"]:
                if (type(part) is not dict or
                        set(part)!={"kind","index","start_char","end_char",
                                   "full_char_count","text","source_timing"} or
                        part["kind"] not in ("title","description","hashtag",
                                             "transcript","scene") or
                        type(part["index"]) is not int or part["index"]<0 or
                        type(part["start_char"]) is not int or
                        type(part["end_char"]) is not int or
                        type(part["full_char_count"]) is not int or
                        type(part["text"]) is not str or
                        part["start_char"]<0 or part["end_char"]>part["full_char_count"]
                        or part["start_char"]>part["end_char"] or
                        len(part["text"])!=part["end_char"]-part["start_char"]):
                    reject("opus.coverage","corrupt source-offset spans",
                           "snapshot_mismatch")
        # Reject fabricated or ill-typed media timings even when the
        # attacker re-hashes every request/manifest field.
        for _,body in entries:
            for part in body["chunk"]["parts"]:
                time=part["source_timing"]
                if part["kind"] in ("transcript","scene"):
                    if (type(time) is not dict or
                            set(time)!={"start_ms","end_ms"} or
                            type(time["start_ms"]) is not int or
                            type(time["end_ms"]) is not int or
                            time["start_ms"]<0 or time["end_ms"]<time["start_ms"]):
                        reject("opus.coverage","invalid observed media timing",
                               "snapshot_mismatch")
                elif time is not None:
                    reject("opus.coverage","nonmedia source timing fabricated",
                           "snapshot_mismatch")
        # Group-level exact continuity independently of the original D1 doc.
        # Cross-verified to authoritative source by rebuilt M20 in live mode.
        atoms={}
        for _,b in entries:
            for part in b["chunk"]["parts"]:
                atoms.setdefault((part["kind"],part["index"]),[]).append(part)
        for parts in atoms.values():
            offset=0
            for part in parts:
                if part["start_char"]!=offset:
                    reject("opus.coverage","missing/overlapping observed text",
                           "incomplete_coverage")
                offset=part["end_char"]
            if offset!=parts[0]["full_char_count"]:
                reject("opus.coverage","incomplete observed source field",
                       "incomplete_coverage")
    flat=[x for batch in batches for x in batch.get("request_ordinals",[])]
    hashes=[h for batch in batches for h in batch.get("request_sha256s",[])]
    if flat!=list(range(len(reqs))) or hashes!=[
        r["request_sha256"] for r in reqs
    ]:
        reject("opus.batches","duplicate/missing planned requests",
               "incomplete_coverage")
    for i,b in enumerate(batches):
        if (type(b) is not dict or
                b.get("batch_ordinal")!=i or
                b.get("batch_sha256")!=fingerprint({
                    k:v for k,v in b.items() if k!="batch_sha256"
                }) or b.get("dispatch_allowed") is not False or
                b.get("paid_calls")!=0 or
                b.get("request_count")!=len(b["request_ordinals"]) or
                b.get("proxy_units_sum")!=sum(
                    reqs[j]["input_token_proxy_units"] for j in b["request_ordinals"]
                )):
            reject("opus.batches","tampered batch manifest","snapshot_mismatch")
    return grouped


def _verify_evidence(raw, offered):
    record(raw,"opus.evidence",required=_EVIDENCE)
    kind=raw["source"]
    idx=raw["index"]
    start=raw["start_char"]
    end=raw["end_char"]
    quote=raw["quote"]
    if (type(kind) is not str or
            kind not in ("title","description","hashtag","transcript","scene") or
            type(idx) is not int or idx<0 or
            type(start) is not int or type(end) is not int or
            type(quote) is not str or not quote.strip() or
            not 0<=start<end):
        reject("opus.evidence","invalid D1 evidence offsets or quote",
               "invalid_evidence")
    matched=False
    for part in offered:
        if (part["kind"]!=kind or part["index"]!=idx or
                not (part["start_char"]<=start<end<=part["end_char"])):
            continue
        relative=start-part["start_char"]
        if part["text"][relative:relative+(end-start)]!=quote:
            continue
        times=part["source_timing"]
        if times is None:
            if raw["start_ms"] is not None or raw["end_ms"] is not None:
                reject("opus.evidence","fabricated source timestamp",
                       "invalid_evidence")
        else:
            if (type(raw["start_ms"]) is not int or
                    type(raw["end_ms"]) is not int or
                    raw["start_ms"]!=times["start_ms"] or
                    raw["end_ms"]!=times["end_ms"]):
                reject("opus.evidence","source timing is not observed",
                       "invalid_evidence")
        matched=True
        break
    if not matched:
        reject("opus.evidence","quote not verbatim in this specific source slice",
               "invalid_evidence")
    return {
        "source":kind,"index":idx,"start_char":start,"end_char":end,
        "quote":quote,"start_ms":raw["start_ms"],"end_ms":raw["end_ms"],
    }


def _verify_single(request, payload, raw_response):
    response=_parse_raw(raw_response)
    record(response,"opus.response",required=_REPLY)
    if response["category_key"]!=request["category_key"]:
        reject("opus.response","unexpected C9 category UUID",
               "unexpected_category")
    canonical_uuid(response["category_key"],"opus.response.category_key")
    if type(response["decision"]) is not str or response["decision"] not in DECISIONS:
        reject("opus.response.decision","only match/no_match/uncertain; "
               "not_evaluated is system-only","invalid_decision")
    reason=response["reason"]
    if (type(reason) is not str or not reason.strip() or
            len(reason)>MAX_REASON_CHARS):
        reject("opus.response.reason","missing/oversized reason",
               "malformed_model_output")
    evidence=response["evidence"]
    if (type(evidence) is not list or
            len(evidence)>MAX_EVIDENCE_PER_CHUNK or
            (response["decision"]=="match" and not evidence)):
        reject("opus.response.evidence","missing/excessive citations",
               "invalid_evidence")
    offered=payload["chunk"]["parts"]
    verified=[_verify_evidence(e,offered) for e in evidence]
    fingerprints=[fingerprint(e) for e in verified]
    if len(fingerprints)!=len(set(fingerprints)):
        reject("opus.response.evidence","duplicate evidence", "invalid_evidence")
    return {
        "category_key":response["category_key"],
        "decision":response["decision"],
        "reason":reason,
        "evidence":verified,
        "request_sha256":request["request_sha256"],
        "request_ordinal":request["request_ordinal"],
        "chunk_index":request["chunk_index"],
        "chunk_count":request["chunk_count"],
        "result_is_semantic_ground_truth":False,
    }


def verify_opus_responses(m20, received):
    """Verify EXACT complete set of technical replies and evidence.

    Technical defects raise SREContractError. NEVER invent no_match for a
    timeout, malformed reply, missing evidence, duplicate or partial chunk.
    This is an untrusted *model-output transport check*, not a semantic gold
    label, provider receipt authentication or permission to publish.
    """
    # The model/client side can submit malformed nested metadata. Report
    # a typed technical violation instead of a Python KeyError/TypeError and
    # NEVER downgrade it into a semantic no_match.
    try:
        grouped=_manifest(m20)
    except (KeyError,IndexError,TypeError,AttributeError,ValueError,OverflowError):
        reject("opus.plan", "corrupt nested request/batch/source manifest",
               "snapshot_mismatch")
    requests=m20["requests"]
    if (type(received) is not list or len(received)!=len(requests) or
            len(received)>MAX_RESPONSES):
        reject("opus.responses","missing/extra/partial model replies",
               "incomplete_coverage")
    by_hash={}
    for item in received:
        record(item,"opus.receipt",required=("request_sha256","raw_json"))
        key=sha256_digest(item["request_sha256"],"opus.receipt.request_sha256")
        if key in by_hash:
            reject("opus.receipt","duplicate request reply",
                   "incomplete_coverage")
        by_hash[key]=item["raw_json"]
    if set(by_hash)!={r["request_sha256"] for r in requests}:
        reject("opus.receipts","responses for unknown or missing requests",
               "incomplete_coverage")
    rows={}
    for key in m20["expected_hypothesis_keys"]:
        rows[key]=[]
        for request,payload in grouped[key]:
            checked=_verify_single(request,payload,by_hash[request["request_sha256"]])
            rows[key].append(checked)
    final=[]
    for key in m20["expected_hypothesis_keys"]:
        pieces=rows[key]
        if len(pieces)==1:
            decision=pieces[0]["decision"]
            review=decision=="uncertain"
        else:
            unique={p["decision"] for p in pieces}
            decision=next(iter(unique)) if len(unique)==1 else "uncertain"
            review=decision=="uncertain"
        # Matching a slice does not remove the need to check ALL other chunks.
        # Mixed label outcomes become uncertain instead of an invented
        # cross-chunk semantic inference.
        item={
            "category_key":key,
            "decision":decision,
            "requires_human_review":review,
            "chunk_count":len(pieces),
            "verified_chunk_count":len(pieces),
            "partial_decisions":[p["decision"] for p in pieces],
            "evidence":[e for p in pieces for e in p["evidence"]],
            "result_is_semantic_ground_truth":False,
            "technical_state":"validated_untrusted_model_output",
        }
        item["category_result_sha256"]=fingerprint(item)
        final.append(item)
    if len(final)!=len(m20["expected_hypothesis_keys"]):
        reject("opus.coverage","final output lost approved C9 category",
               "incomplete_coverage")
    result={
        "schema_version":VERSION,
        "source_batch_manifest_sha256":m20["batch_manifest_sha256"],
        "model_id":m20["model_id"],
        "model_revision":m20["model_revision"],
        "catalog_revision":m20["catalog_revision"],
        "catalog_sha256":m20["catalog_sha256"],
        "project_id":m20["project_id"],
        "publication_id":m20["publication_id"],
        "video_id":m20["video_id"],
        "content_version_id":m20["content_version_id"],
        "content_sha256":m20["content_sha256"],
        "expected_hypothesis_keys":list(m20["expected_hypothesis_keys"]),
        "expected_request_count":len(requests),
        "verified_request_count":len(requests),
        "expected_category_count":len(final),
        "verified_category_count":len(final),
        "dropped_count":0,
        "results":final,
        "provider_receipts_authenticated":False,
        "semantics_independently_confirmed":False,
        "dispatch_allowed":False,
        "paid_calls":0,
    }
    result["verification_sha256"]=fingerprint(result)
    return result


def verify_live_opus_responses(conn,project,run_id,item_id,received, *,
                               operator_catalog_pin,
                               max_input_token_proxy=None,
                               expected_batch_manifest_sha256):
    """Rebuild authoritative M20 from live C9+D1 before verifying replies.

    WARNING: project ID is not an authentication mechanism. This is a
    trusted internal port requiring host authentication; verify receipt
    provenance in the future audited sender. No D1 external send rights or
    paid consent are implied by local validation of response text.
    """
    pid=project_id(project)
    sha256_digest(expected_batch_manifest_sha256,
                  "expected_batch_manifest_sha256")
    m20=pack_live_opus_requests(
        conn,pid,run_id,item_id,
        operator_catalog_pin=operator_catalog_pin,
        max_input_token_proxy=max_input_token_proxy,
    )
    if m20["batch_manifest_sha256"]!=expected_batch_manifest_sha256:
        reject("opus.live","run/plan/model/content changed since request",
               "snapshot_mismatch")
    return verify_opus_responses(m20,received)


__all__=(
    "VERSION","verify_opus_responses","verify_live_opus_responses",
)
