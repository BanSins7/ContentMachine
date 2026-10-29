"""M20 deterministic Opus transport plan with complete C9 and lossless D1 slices.

Pure, provider-free planner. This is NOT an Opus tokenizer, quote, consent or
paid dispatch. UTF-8 byte count is a deliberately pessimistic budget proxy,
NOT verified provider token usage. Actual model/catalog validation, preflight
token checking, current source rights, worker fencing and finance approval
must occur in a separate future dispatch gate.

Runtime entry reads M19 envelopes only from DB-approved C9 and current D1.
User/configuration-facing pure helpers never confer C9/ownership authority.
"""
from __future__ import annotations

import json
import re

from .live_classifier_context import (
    CONTEXT_VERSION, SYSTEM_INSTRUCTION, OUTPUT_FIELDS,
    build_live_classifier_context,
)
from .v1_contracts import (
    canonical_json, canonical_uuid, fingerprint, positive, project_id,
    record, reject, sha256_digest,
)

PACK_VERSION = "sre-opus-request-pack/v1"
CATALOG_VERSION = "sre-opus-catalog-pin/v1"
MAX_LEAVES = 48
MAX_REQUESTS = 2048
MAX_BATCH_REQUESTS = 16
MAX_BATCH_PROXY_UNITS = 250000
MAX_UTF8_PROXY = 250000
MIN_UTF8_PROXY = 4096
PER_REQUEST_OVERHEAD = 512  # pessimistic non-content framing reserve, in byte units
MODEL_PATTERN = re.compile(r"anthropic/claude-opus-[a-z0-9][a-z0-9.-]{1,95}\Z")
REV_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{5,119}\Z")
LATEST_PATTERN = re.compile(r"(?:latest|current|auto|default|floating)",re.I)
PART_TYPES = ("title","description","hashtag","transcript","scene")


def validate_catalog_pin(pin):
    """Trusted operator's *exact* snapshot input; never self-authenticating."""
    fields=(
        "schema_version","provider","model_id","model_revision",
        "catalog_revision","catalog_sha256",
        "context_window_tokens","max_output_tokens","max_input_token_proxy",
        "temperature_milli",
    )
    record(pin,"opus_catalog",required=fields)
    if pin["schema_version"]!=CATALOG_VERSION or pin["provider"]!="openrouter":
        reject("opus_catalog","unsupported Opus provider/catalog schema",
               "unsupported_schema_version")
    model=pin["model_id"]
    if type(model) is not str or not MODEL_PATTERN.fullmatch(model):
        reject("opus_catalog.model_id","expected pinned Anthropic Opus model")
    for key in ("model_revision","catalog_revision"):
        value=pin[key]
        if (type(value) is not str or not REV_PATTERN.fullmatch(value)
                or LATEST_PATTERN.search(value)):
            reject("opus_catalog."+key,"floating or invalid revision forbidden")
    sha256_digest(pin["catalog_sha256"],"opus_catalog.catalog_sha256")
    context=positive(pin["context_window_tokens"],
                     "context_window_tokens",maximum=2000000)
    output=positive(pin["max_output_tokens"],
                    "max_output_tokens",maximum=128000)
    bound=positive(pin["max_input_token_proxy"],
                   "max_input_token_proxy",maximum=MAX_UTF8_PROXY)
    if (context<=output+1024 or bound<MIN_UTF8_PROXY or
            bound>context-output-1024):
        reject("opus_catalog","invalid context/output/proxy safety envelope",
               "token_budget_exceeded")
    if type(pin["temperature_milli"]) is not int or pin["temperature_milli"]!=0:
        reject("opus_catalog.temperature_milli",
               "must use deterministic requested temperature 0")
    return dict(pin)


def _seal(packet):
    if type(packet) is not dict:
        reject("opus.packet","invalid M19 packet")
    exact={
        "schema_version","project_id","publication_id","video_id",
        "content_version_id","content_sha256","category_key",
        "context_sha256","c9_bundle_sha256","system_instruction_sha256",
        "user_payload_sha256","messages","output_fields","output_contract",
        "external_dispatch_allowed","paid_calls","packet_sha256",
    }
    if set(packet)!=exact or packet["schema_version"]!=CONTEXT_VERSION:
        reject("opus.packet","invalid M19 packet format","snapshot_mismatch")
    if (packet["external_dispatch_allowed"] is not False or
            packet["paid_calls"]!=0 or packet["output_fields"]!=list(OUTPUT_FIELDS)):
        reject("opus.packet","M19 packet cannot grant dispatch or omit output fields",
               "rights_denied")
    if packet["packet_sha256"]!=fingerprint({
        k:v for k,v in packet.items() if k!="packet_sha256"
    }):
        reject("opus.packet","M19 packet fingerprint differs","snapshot_mismatch")
    messages=packet["messages"]
    if (type(messages) is not list or len(messages)!=2 or
            messages[0]!={"role":"system","content":SYSTEM_INSTRUCTION} or
            type(messages[1]) is not dict or
            set(messages[1])!={"role","content"} or
            messages[1]["role"]!="user" or
            type(messages[1]["content"]) is not str):
        reject("opus.messages","system role/prompt tampering","snapshot_mismatch")
    try:
        payload=json.loads(messages[1]["content"])
    except (ValueError,TypeError):
        reject("opus.messages","invalid M19 canonical JSON","snapshot_mismatch")
    if (messages[1]["content"]!=canonical_json(payload) or
            packet["user_payload_sha256"]!=fingerprint(payload) or
            packet["system_instruction_sha256"]!=fingerprint(SYSTEM_INSTRUCTION)):
        reject("opus.messages","M19 JSON or instructions not sealed",
               "snapshot_mismatch")
    required={
        "schema_version","task","expected_category_key","source_pins",
        "approved_taxonomy_data","video_observation_data","trust_boundary",
    }
    if type(payload) is not dict or set(payload)!=required or payload["schema_version"]!=CONTEXT_VERSION:
        reject("opus.payload","invalid original C9/D1 context format",
               "snapshot_mismatch")
    c9=payload["approved_taxonomy_data"]
    pins=payload["source_pins"]
    if (type(c9) is not dict or type(pins) is not dict or
            c9.get("context_sha256")!=packet["context_sha256"] or
            c9.get("category_key")!=packet["category_key"] or
            payload["expected_category_key"]!=packet["category_key"] or
            pins.get("project_id")!=packet["project_id"] or
            pins.get("publication_id")!=packet["publication_id"] or
            pins.get("content_version_id")!=packet["content_version_id"] or
            pins.get("content_sha256")!=packet["content_sha256"] or
            pins.get("c9_bundle_sha256")!=packet["c9_bundle_sha256"]):
        reject("opus.payload","context and source pins not coherent",
               "snapshot_mismatch")
    if (type(payload["trust_boundary"]) is not dict or
            payload["trust_boundary"].get("data_only") is not True or
            payload["trust_boundary"].get("external_llm_send_authorized") is not False):
        reject("opus.payload","untrusted DATA role elevated","rights_denied")
    return payload


def _atoms(observations):
    """Observed field/text chunks in deterministic source order, no omission.

    Index+offset is provenance and allows complete lossless reconstruction.
    Timestamps on transcript/scene are original source values; never guessed.
    """
    if type(observations) is not dict:
        reject("opus.observations","missing D1 observations","snapshot_mismatch")
    atoms=[]
    def put(kind,index,text,meta=None):
        if type(text) is not str:
            reject("opus.observations","non-text observed field","snapshot_mismatch")
        atoms.append({"kind":kind,"index":index,"start_char":0,"end_char":len(text),
                      "full_char_count":len(text),"text":text,
                      "source_timing":meta})
    for kind in ("title","description"):
        if observations.get(kind) is not None:
            put(kind,0,observations[kind])
    for i,text in enumerate(observations.get("hashtags",[])):
        put("hashtag",i,text)
    for kind,field in (("transcript","transcript"),("scene","scenes")):
        segments=observations.get(field)
        if segments is not None:
            for i,seg in enumerate(segments):
                if type(seg) is not dict or not {"text","start_ms","end_ms"}.issubset(seg):
                    reject("opus.observations","broken measured media segment",
                           "snapshot_mismatch")
                put(kind,i,seg["text"],
                    {"start_ms":seg["start_ms"],"end_ms":seg["end_ms"]})
    return atoms


def _chunk_payload(original, segments, *, chunk_index, chunk_count, observations_sha):
    """Always repeats exact full C9 context; ONLY D1 observations may split."""
    original_meta=original["video_observation_data"]
    header={
        k:v for k,v in original_meta.items() if k not in (
            "title","description","hashtags","transcript","scenes"
        )
    }
    header["original_observations_sha256"]=observations_sha
    header["original_fields_sha256"]=fingerprint(original_meta)
    header["source_field_counts"]={
        "title":0 if original_meta["title"] is None else 1,
        "description":0 if original_meta["description"] is None else 1,
        "hashtag":len(original_meta["hashtags"]),
        "transcript":0 if original_meta["transcript"] is None else len(original_meta["transcript"]),
        "scene":0 if original_meta["scenes"] is None else len(original_meta["scenes"]),
    }
    return {
        "schema_version":PACK_VERSION,
        "task":"one_category_one_observation_slice",
        "expected_category_key":original["expected_category_key"],
        "source_pins":original["source_pins"],
        "approved_taxonomy_data":original["approved_taxonomy_data"],
        "observation_header":header,
        "chunk":{"index":chunk_index,"count":chunk_count,"parts":segments},
        "trust_boundary":original["trust_boundary"],
        "output_instruction":{
            "partial_evidence_only":True,
            "requires_aggregate_validation":True,
            "decision_is_not_final_until_all_chunks_verified":True,
        },
    }


def _size(payload):
    """Conservative byte proxy; not a real Opus tokenizer."""
    return (len(canonical_json(payload).encode("utf-8"))+
            len(SYSTEM_INSTRUCTION.encode("utf-8"))+PER_REQUEST_OVERHEAD)


def _split_parts(original, limit):
    observations=original["video_observation_data"]
    source=_atoms(observations)
    obs_sha=fingerprint(observations)
    # Use worst-case chunk digits; no later JSON shape can silently exceed.
    dummy=_chunk_payload(original,[],chunk_index=MAX_REQUESTS,chunk_count=MAX_REQUESTS,
                         observations_sha=obs_sha)
    if _size(dummy)>limit:
        reject("opus.context","C9/system/source pins alone exceed input budget; "
               "must explicitly revise upstream, not drop approved facts",
               "token_budget_exceeded")
    result=[]
    current=[]
    def fits(parts):
        return _size(_chunk_payload(original,parts,chunk_index=MAX_REQUESTS,
                                    chunk_count=MAX_REQUESTS,
                                    observations_sha=obs_sha))<=limit
    def flush():
        nonlocal current
        if current:
            result.append(current)
            current=[]
    for atom in source:
        text=atom["text"]
        pos=0
        if text=="":
            if not fits(current+[atom]):
                flush()
            if not fits([atom]):
                reject("opus.chunk","empty field header exceeds budget",
                       "token_budget_exceeded")
            current.append(atom)
            continue
        while pos<len(text):
            if not fits(current+[{
                **atom,"start_char":pos,"end_char":len(text),
                "text":text[pos:],
            }]):
                if current:
                    flush()
                    continue
                # Binary search maximal whole Unicode codepoint span. JSON/UTF8
                # size checked on every candidate so no escaped chars vanish.
                lo,hi=pos+1,len(text)
                best=pos
                while lo<=hi:
                    mid=(lo+hi)//2
                    candidate={**atom,"start_char":pos,"end_char":mid,
                               "text":text[pos:mid]}
                    if fits([candidate]):
                        best=mid
                        lo=mid+1
                    else:
                        hi=mid-1
                if best==pos:
                    reject("opus.chunk","single source character cannot fit "
                           "without modifying approved C9 context",
                           "token_budget_exceeded")
                current.append({
                    **atom,"start_char":pos,"end_char":best,
                    "text":text[pos:best],
                })
                pos=best
                flush()
            else:
                current.append({
                    **atom,"start_char":pos,"end_char":len(text),
                    "text":text[pos:],
                })
                pos=len(text)
    flush()
    return result or [[]]


def _check_lossless(observations,parts):
    """Lossless per source field even when split mid-codepoint sequence."""
    originals=_atoms(observations)
    expected={(a["kind"],a["index"]):a for a in originals}
    received={}
    for group in parts:
        for atom in group:
            key=(atom["kind"],atom["index"])
            if key not in expected:
                reject("opus.coverage","unexpected D1 field","incomplete_coverage")
            original=expected[key]
            if any(atom[k]!=original[k] for k in ("kind","index",
                    "full_char_count","source_timing")):
                reject("opus.coverage","D1 provenance changed","snapshot_mismatch")
            received.setdefault(key,[]).append(atom)
    if set(expected)!=set(received):
        reject("opus.coverage","D1 source text lost","incomplete_coverage")
    for key,seq in received.items():
        pos=0
        for a in seq:
            if (a["start_char"]!=pos or a["end_char"]<=pos and
                    a["full_char_count"]>0 or
                    a["end_char"]-a["start_char"]!=len(a["text"])):
                reject("opus.coverage","overlap or gap in D1 source span",
                       "incomplete_coverage")
            pos=a["end_char"]
        if pos!=expected[key]["full_char_count"] or "".join(x["text"] for x in seq)!=expected[key]["text"]:
            reject("opus.coverage","source text changed or incomplete",
                   "incomplete_coverage")


def _batches(requests):
    """Deterministic LOCAL work groups, not OpenRouter's remote batch API.

    Stable ordinals and request hashes make retries/replays auditable. Group
    limits apply independently from per-request model context/token preflight.
    """
    result=[]
    current=[]
    units=0
    def flush():
        nonlocal current,units
        if not current:
            return
        batch={
            "schema_version":PACK_VERSION,
            "batch_ordinal":len(result),
            "request_ordinals":[r["request_ordinal"] for r in current],
            "request_sha256s":[r["request_sha256"] for r in current],
            "category_keys":[r["category_key"] for r in current],
            "proxy_units_sum":units,
            "request_count":len(current),
            "dispatch_allowed":False,
            "paid_calls":0,
        }
        batch["batch_sha256"]=fingerprint(batch)
        result.append(batch)
        current=[]
        units=0
    for request in requests:
        size=request["input_token_proxy_units"]
        if size>MAX_BATCH_PROXY_UNITS:
            reject("opus.batch","one request exceeds local batch proxy cap",
                   "token_budget_exceeded")
        if current and (len(current)==MAX_BATCH_REQUESTS or
                        units+size>MAX_BATCH_PROXY_UNITS):
            flush()
        current.append(request)
        units+=size
    flush()
    ordinals=[idx for batch in result for idx in batch["request_ordinals"]]
    hashes=[item for batch in result for item in batch["request_sha256s"]]
    if (ordinals!=list(range(len(requests))) or
            hashes!=[r["request_sha256"] for r in requests]):
        reject("opus.batch","missing, duplicate or reordered request",
               "incomplete_coverage")
    return result


def pack_opus_requests(m19, operator_catalog_pin, *,
                       max_input_token_proxy=None):
    """Pure plan. No Opus call or actual token accounting claim.

    The catalog revision/sha MUST come from authenticated trusted operator
    configuration. This routine only validates shape and snapshots; it is
    not a network-backed catalog authentication or permission gate.
    """
    catalog=validate_catalog_pin(operator_catalog_pin)
    if type(m19) is not dict:
        reject("opus.m19","missing assembled classifier context","snapshot_mismatch")
    keys=m19.get("expected_hypothesis_keys")
    packets=m19.get("packets")
    if (type(keys) is not list or not 1<=len(keys)<=MAX_LEAVES or
            keys!=sorted(set(keys)) or type(packets) is not list or
            len(keys)!=len(packets) or
            m19.get("packet_count")!=len(keys) or
            m19.get("expected_packet_count")!=len(keys) or
            m19.get("dropped_count")!=0 or
            m19.get("all_leaf_required") is not True or
            m19.get("external_dispatch_allowed") is not False or
            m19.get("paid_calls")!=0):
        reject("opus.m19","incomplete or unsafe classifier manifest",
               "incomplete_coverage")
    if m19.get("manifest_sha256")!=fingerprint({
        k:v for k,v in m19.items() if k!="manifest_sha256"
    }):
        reject("opus.m19","M19 manifest integrity differs","snapshot_mismatch")
    actual=[canonical_uuid(p.get("category_key"),"category_key")
            if type(p) is dict else "" for p in packets]
    if keys!=actual:
        reject("opus.coverage","M19 packets are not every approved leaf",
               "incomplete_coverage")
    budget=catalog["max_input_token_proxy"]
    if max_input_token_proxy is not None:
        b=positive(max_input_token_proxy,"max_input_token_proxy",
                   maximum=MAX_UTF8_PROXY)
        if b<MIN_UTF8_PROXY or b>budget:
            reject("opus.budget","caller may only reduce trusted token proxy budget",
                   "token_budget_exceeded")
        budget=b
    requests=[]
    slices=[]
    for packet in packets:
        payload=_seal(packet)
        if (packet["project_id"]!=m19.get("project_id") or
                packet["publication_id"]!=m19.get("publication_id") or
                packet["content_version_id"]!=m19.get("content_version_id") or
                packet["content_sha256"]!=m19.get("content_sha256") or
                packet["c9_bundle_sha256"]!=m19.get("c9_bundle_sha256")):
            reject("opus.m19","cross-leaf source pins differ",
                   "snapshot_mismatch")
        groups=_split_parts(payload,budget)
        if len(groups)>MAX_REQUESTS or len(groups)+len(requests)>MAX_REQUESTS:
            reject("opus.requests","too many source slices","token_budget_exceeded")
        _check_lossless(payload["video_observation_data"],groups)
        slices.append({
            "category_key":packet["category_key"],
            "packet_sha256":packet["packet_sha256"],
            "chunk_count":len(groups),
            "source_observations_sha256":fingerprint(payload["video_observation_data"]),
        })
        for idx,group in enumerate(groups):
            send=_chunk_payload(
                payload,group,chunk_index=idx,chunk_count=len(groups),
                observations_sha=fingerprint(payload["video_observation_data"]),
            )
            proxy=_size(send)
            if proxy>budget:
                reject("opus.budget","chunk exceeds trusted input byte proxy",
                       "token_budget_exceeded")
            request={
                "schema_version":PACK_VERSION,
                "request_ordinal":len(requests),
                "category_key":packet["category_key"],
                "chunk_index":idx,"chunk_count":len(groups),
                "original_packet_sha256":packet["packet_sha256"],
                "system_sha256":packet["system_instruction_sha256"],
                "model_id":catalog["model_id"],
                "model_revision":catalog["model_revision"],
                "catalog_revision":catalog["catalog_revision"],
                "catalog_sha256":catalog["catalog_sha256"],
                "max_output_tokens":catalog["max_output_tokens"],
                "temperature_milli":0,
                "messages":[
                    {"role":"system","content":SYSTEM_INSTRUCTION},
                    {"role":"user","content":canonical_json(send)},
                ],
                "input_token_proxy_units":proxy,
                "token_count_exact":False,
                "requires_provider_preflight":True,
                "dispatch_allowed":False,
                "paid_calls":0,
            }
            request["request_sha256"]=fingerprint(request)
            requests.append(request)
    if {r["category_key"] for r in requests}!=set(keys):
        reject("opus.coverage","C9 hypothesis lost","incomplete_coverage")
    groups=_batches(requests)
    descriptor={
        "schema_version":PACK_VERSION,
        "model_id":catalog["model_id"],
        "model_revision":catalog["model_revision"],
        "catalog_revision":catalog["catalog_revision"],
        "catalog_sha256":catalog["catalog_sha256"],
        "catalog_pin_sha256":fingerprint(catalog),
        "source_manifest_sha256":m19["manifest_sha256"],
        "project_id":m19["project_id"],
        "publication_id":m19["publication_id"],
        "video_id":m19["video_id"],
        "content_version_id":m19["content_version_id"],
        "content_sha256":m19["content_sha256"],
        "expected_hypothesis_keys":list(keys),
        "hypothesis_coverage":slices,
        "hypothesis_count":len(keys),
        "request_count":len(requests),
        "batch_count":len(groups),
        "batch_request_limit":MAX_BATCH_REQUESTS,
        "batch_proxy_limit":MAX_BATCH_PROXY_UNITS,
        "batches":groups,
        "dropped_count":0,
        "input_token_budget_proxy":budget,
        "token_count_exact":False,
        "requires_provider_preflight":True,
        "dispatch_allowed":False,
        "paid_calls":0,
        "requests":requests,
    }
    descriptor["batch_manifest_sha256"]=fingerprint(descriptor)
    return descriptor


def pack_live_opus_requests(conn,project,run_id,item_id,*,operator_catalog_pin,
                            max_input_token_proxy=None):
    """Trusted DB gate: no payment, outbound model request or implicit consent.

    Project authentication must be enforced at host boundary; M20 itself
    reuses M19 genuine project+publication+source-pinned LIVE reads.
    """
    pid=project_id(project)
    manifest=build_live_classifier_context(conn,pid,run_id,item_id,split_ready=True)
    return pack_opus_requests(
        manifest,operator_catalog_pin,
        max_input_token_proxy=max_input_token_proxy,
    )


__all__=[
    "PACK_VERSION","CATALOG_VERSION","SYSTEM_INSTRUCTION",
    "validate_catalog_pin","pack_opus_requests","pack_live_opus_requests",
]
