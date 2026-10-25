"""B2 Stage05: exact-cover, bounded Multi-Pack planner and indicative USD budget.

Input is a verified immutable B1 bundle and exact original text. Output is
metadata-only pack manifests and reproducible UTF-8 JSONL bytes. No external
calls or spending authorization. Stages 06/07 MUST preflight the exact final
model request and obtain independent explicit budget/consent.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_CEILING
import hashlib

from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.taxonomy_builder.cost_estimator import rates, amount
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from .full_pack import (
    MODEL_ID, PACK_VERSION, MAX_PACK_BYTES, WIRE_OVERHEAD, BYTE_MULTIPLIER,
    _json, _hash_bytes, _line, _header, _decision, normalize_config,
    normalize_snapshot,
)
from .inventory import check_fragment_text
from .intake import IntakeError

VERSION = "evidence-multi-pack-plan/v1"
PACK_SCHEMA = "evidence-multi-pack/v1"
PLANNER = "ke-multi-pack/1.0.0"
MAX_PARTS = 256
MAX_SPANS = 100_000
MAX_TOTAL_PAYLOAD_BYTES = 64_000_000
PRICE_QUANTUM = Decimal("0.00000001")
BUFFER = Decimal("1.20")


def _usd(value):
    return format(value.quantize(PRICE_QUANTUM, rounding=ROUND_CEILING), "f")


def normalize_multi_config(raw):
    if not isinstance(raw, dict):
        raise IntakeError("multi_pack_config_invalid")
    core_names = {"max_output_tokens", "instruction_reserve_tokens",
                  "schema_reserve_tokens", "safety_margin_tokens"}
    if set(raw) != core_names | {"max_packs", "budget_cap_usd"}:
        raise IntakeError("multi_pack_config_invalid")
    core = normalize_config({k: raw[k] for k in core_names})
    limit = raw["max_packs"]
    if type(limit) is not int or not 2 <= limit <= MAX_PARTS:
        raise IntakeError("multi_pack_limit_invalid")
    try:
        ceiling = amount(raw["budget_cap_usd"], "B multi-pack advisory ceiling")
    except PipelineError as exc:
        raise IntakeError("multi_pack_budget_invalid") from exc
    if (ceiling <= 0 or ceiling > 10000 or
        ceiling != ceiling.quantize(PRICE_QUANTUM, rounding=ROUND_CEILING)):
        # Never increase the user's stated budget by upward rounding.
        raise IntakeError("multi_pack_budget_invalid")
    return {**core, "max_packs": limit, "budget_cap_usd": _usd(ceiling)}


def _chunk_line(fragment, text, begin, end):
    chunk = text[begin:end]
    if not chunk or begin < 0 or end > len(text):
        raise IntakeError("invalid_fragment_span")
    original_id = fragment["fragment_id"]
    digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    chunk_id = canonical_sha({"fragment_id": original_id, "char_start": begin,
                              "char_end": end, "text_sha256": digest})
    line = _line(fragment, chunk)
    line.update({"kind": "text_span", "chunk_id": chunk_id,
                 "original_fragment_id": original_id,
                 "original_text_sha256": fragment["text_sha256"],
                 "char_start": begin, "char_end": end,
                 "exact_text_sha256": digest})
    return chunk_id, digest, (_json(line) + "\n").encode("utf-8")


def _part_header(bundle, index):
    value = _header(bundle)
    value.update({"kind": "multi_pack_header", "pack_schema": PACK_SCHEMA,
                  "pack_index": index, "planner_version": PLANNER})
    return (_json(value) + "\n").encode("utf-8")


def verify_multi_pack_payload(manifest, payload):
    if not isinstance(manifest, dict) or not isinstance(payload, bytes):
        raise IntakeError("multi_pack_payload_invalid")
    if manifest.get("schema_version") != PACK_SCHEMA or (
        len(payload) != manifest.get("payload_bytes")
        or _hash_bytes(payload) != manifest.get("payload_sha256")
    ):
        raise IntakeError("multi_pack_payload_integrity")
    lines = manifest.get("spans")
    if not isinstance(lines, list) or not lines or len(lines) != manifest.get("span_count"):
        raise IntakeError("multi_pack_manifest_invalid")
    first = payload.find(b"\n") + 1
    if first <= 0 or _hash_bytes(payload[:first]) != manifest.get("header_sha256"):
        raise IntakeError("multi_pack_header_invalid")
    import json
    try:
        header = json.loads(payload[:first])
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntakeError("multi_pack_header_invalid") from exc
    if (header.get("pack_schema") != PACK_SCHEMA or
        header.get("bundle_sha256") != manifest.get("bundle_sha256") or
        header.get("pack_index") != manifest.get("pack_index")):
        raise IntakeError("multi_pack_header_invalid")
    cursor = first
    for entry in lines:
        start, end = entry.get("byte_start"), entry.get("byte_end")
        if (type(start) is not int or type(end) is not int or
            start != cursor or end <= start or end > len(payload)):
            raise IntakeError("multi_pack_offset_invalid")
        raw = payload[start:end]
        if not raw.endswith(b"\n") or _hash_bytes(raw) != entry.get("line_sha256"):
            raise IntakeError("multi_pack_line_integrity")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise IntakeError("multi_pack_line_integrity") from exc
        if (decoded.get("chunk_id") != entry.get("chunk_id") or
            decoded.get("original_fragment_id") != entry.get("fragment_id") or
            decoded.get("char_start") != entry.get("char_start") or
            decoded.get("char_end") != entry.get("char_end") or
            decoded.get("source_role") != entry.get("source_role") or
            decoded.get("source_id") != entry.get("source_id") or
            decoded.get("source_version_id") != entry.get("source_version_id") or
            decoded.get("subject_id") != entry.get("subject_id") or
            decoded.get("locator") != entry.get("locator")):
            raise IntakeError("multi_pack_provenance_integrity")
        text = decoded.get("text")
        if (not isinstance(text, str) or len(text) != entry.get("char_end") - entry.get("char_start")
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != entry.get("text_sha256")
            or decoded.get("exact_text_sha256") != entry.get("text_sha256") or
            decoded.get("original_text_sha256") != entry.get("original_text_sha256")):
            raise IntakeError("multi_pack_span_integrity")
        cursor = end
    if cursor != len(payload):
        raise IntakeError("multi_pack_payload_coverage")
    return True


def _verify_coverage(bundle, parts):
    """Prove ordered disjoint [0,len(text)) per original B1 fragment."""
    expected = {f["fragment_id"]: f for f in bundle["fragments"]}
    positions = {fid: 0 for fid in expected}
    seen = set()
    total = 0
    for index, part in enumerate(parts):
        if part["pack_index"] != index or not part["spans"]:
            raise IntakeError("multi_pack_order_invalid")
        for row in part["spans"]:
            fid = row["fragment_id"]
            if fid not in expected or row["chunk_id"] in seen or (
                row["char_start"] != positions[fid] or
                row["char_end"] <= row["char_start"] or
                row["char_end"] > expected[fid]["char_count"] or
                row["original_text_sha256"] != expected[fid]["text_sha256"] or
                row["source_id"] != expected[fid]["source_id"] or
                row["source_version_id"] != expected[fid]["source_version_id"] or
                row["source_role"] != expected[fid]["source_role"] or
                row["subject_id"] != expected[fid]["subject_id"] or
                row["locator"] != expected[fid]["locator"]):
                raise IntakeError("multi_pack_coverage_invalid")
            seen.add(row["chunk_id"])
            positions[fid] = row["char_end"]
            total += 1
    if positions != {fid: f["char_count"] for fid, f in expected.items()}:
        raise IntakeError("multi_pack_coverage_incomplete")
    return total


def plan_multi_pack(bundle, texts, snapshot, raw_config, *, allow_partial=False):
    """Pure all-or-nothing plan: (decision, [(manifest, raw_utf8_bytes), ...]).

    Never 'fits' if one original source char is missing or duplicated. Oversize
    individual fragments split into exact Unicode character slices; original
    fragment ID, sha256, locator and source identity remain pinned.
    """
    model = normalize_snapshot(snapshot)
    config = normalize_multi_config(raw_config)
    if type(allow_partial) is not bool:
        raise IntakeError("partial_acknowledgment_invalid")
    core = {key: config[key] for key in (
        "max_output_tokens", "instruction_reserve_tokens",
        "schema_reserve_tokens", "safety_margin_tokens")}
    base = _decision(bundle, model, core, allow_partial=allow_partial)
    config_sha = canonical_sha({**config, "allow_partial": allow_partial})
    decision = {"schema_version": "multi-pack-decision/v1",
                "bundle_sha256": canonical_sha(bundle), "project_id": bundle["project_id"],
                "context_id": bundle["context_id"], "config_sha256": config_sha,
                "catalog_version_id": model["catalog_version_id"],
                "catalog_sha256": model["catalog_sha256"], "model_id": MODEL_ID,
                "planner_version": PLANNER, "provider_calls": 0,
                "tokenizer_check": "pending_exact_provider_preflight",
                "partial_acknowledged": allow_partial}
    if base["status"] == "blocked":
        decision.update(status="blocked", reason=base["reason"], pack_count=0)
        return decision, None
    if model["max_provider_output_tokens"] is not None and (
        core["max_output_tokens"] > model["max_provider_output_tokens"]
    ):
        decision.update(status="blocked", reason="model_output_limit", pack_count=0)
        return decision, None
    reserved = sum(core.values()) + WIRE_OVERHEAD
    available = model["context_window_tokens"] - reserved
    per_pack_limit = min(MAX_PACK_BYTES, (available - WIRE_OVERHEAD) // BYTE_MULTIPLIER)
    if per_pack_limit < 1024:
        decision.update(status="blocked", reason="insufficient_context_window", pack_count=0)
        return decision, None
    try:
        price = rates(snapshot.get("pricing"))
    except PipelineError as exc:
        raise IntakeError("multi_pack_pricing_unverified") from exc
    price_sha = canonical_sha(snapshot["pricing"])
    decision["pricing_sha256"] = price_sha
    decision["context_window_tokens"] = model["context_window_tokens"]
    decision["max_payload_bytes_per_pack"] = per_pack_limit
    fragments = bundle["fragments"]
    if (not isinstance(fragments, list) or not fragments or len(fragments)>50_000 or
        len({f["fragment_id"] for f in fragments}) != len(fragments) or
        not isinstance(texts, dict) or set(texts) != {f["fragment_id"] for f in fragments}):
        raise IntakeError("multi_pack_text_coverage_mismatch")
    for f in fragments:
        check_fragment_text(f, texts[f["fragment_id"]])

    packs = []
    header = b""; chunks = []; spans = []; cursor = 0
    total_bytes = 0
    def new_pack():
        nonlocal header, chunks, spans, cursor
        idx = len(packs)
        header = _part_header(bundle, idx)
        if len(header) >= per_pack_limit:
            raise IntakeError("multi_pack_header_exceeds_window")
        chunks = [header]; spans = []; cursor = len(header)

    def finalize():
        nonlocal total_bytes
        if not spans:
            return
        payload = b"".join(chunks)
        estimated_input = BYTE_MULTIPLIER * len(payload) + WIRE_OVERHEAD + (
            core["instruction_reserve_tokens"] + core["schema_reserve_tokens"] +
            core["safety_margin_tokens"])
        if (estimated_input + core["max_output_tokens"] > model["context_window_tokens"]
            or len(payload)>MAX_PACK_BYTES):
            raise IntakeError("multi_pack_capacity_invariant")
        high = (Decimal(estimated_input) * price["prompt"] +
                Decimal(core["max_output_tokens"]) * (
                    price["completion"] + price["internal_reasoning"]) + price["request"]) * BUFFER
        expected = (Decimal(estimated_input) * price["prompt"] +
                    Decimal(max(1, core["max_output_tokens"]//2)) * (
                    price["completion"] + price["internal_reasoning"]) + price["request"])
        manifest = {"schema_version": PACK_SCHEMA, "planner_version": PLANNER,
                    "pack_index": len(packs), "bundle_sha256": canonical_sha(bundle),
                    "context_sha256": bundle["context_sha256"],
                    "header_sha256": _hash_bytes(header),
                    "spans": spans, "span_count": len(spans),
                    "payload_bytes": len(payload), "payload_sha256": _hash_bytes(payload),
                    "input_token_upper_estimate": estimated_input,
                    "max_output_tokens": core["max_output_tokens"],
                    "estimated_usd": _usd(expected), "reserved_usd": _usd(high)}
        verify_multi_pack_payload(manifest, payload)
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL_PAYLOAD_BYTES:
            raise IntakeError("multi_pack_total_bytes_limit")
        packs.append((manifest, payload))
        if len(packs) > min(config["max_packs"], MAX_PARTS):
            raise IntakeError("multi_pack_count_limit")

    new_pack()
    for fragment in fragments:
        text = texts[fragment["fragment_id"]]
        begin = 0
        while begin < len(text):
            # Preserve a complete fragment if it fits in the current part;
            # otherwise begin a new part before splitting its contents.
            chunk_id, chunk_sha, encoded = _chunk_line(fragment, text, begin, len(text))
            if cursor + len(encoded) > per_pack_limit:
                if spans:
                    finalize()
                    new_pack()
                    chunk_id, chunk_sha, encoded = _chunk_line(fragment, text, begin, len(text))
                if cursor + len(encoded) > per_pack_limit:
                    low, high = begin + 1, len(text)
                    best = None
                    while low <= high:
                        mid = (low + high) // 2
                        candidate = _chunk_line(fragment, text, begin, mid)
                        if cursor + len(candidate[2]) <= per_pack_limit:
                            best = (mid, candidate)
                            low = mid + 1
                        else:
                            high = mid - 1
                    if best is None:
                        raise IntakeError("multi_pack_character_exceeds_window")
                    end, (chunk_id, chunk_sha, encoded) = best
                else:
                    end = len(text)
            else:
                end = len(text)
            if len(packs)>=min(config["max_packs"],MAX_PARTS):
                raise IntakeError("multi_pack_count_limit")
            span = {"chunk_id": chunk_id, "fragment_id": fragment["fragment_id"],
                    "source_id": fragment["source_id"],
                    "source_version_id": fragment["source_version_id"],
                    "output_id": fragment["output_id"],
                    "locator": fragment["locator"],
                    "source_role": fragment["source_role"],
                    "subject_id": fragment["subject_id"],
                    "original_text_sha256": fragment["text_sha256"],
                    "text_sha256": chunk_sha,
                    "char_start": begin, "char_end": end,
                    "byte_start": cursor, "byte_end": cursor+len(encoded),
                    "line_sha256": _hash_bytes(encoded)}
            spans.append(span);chunks.append(encoded);cursor+=len(encoded)
            if sum(len(p[0]["spans"]) for p in packs)+len(spans)>MAX_SPANS:
                raise IntakeError("multi_pack_span_limit")
            begin=end
            if begin < len(text):
                finalize()
                new_pack()
    finalize()
    coverage_spans = _verify_coverage(bundle, [p[0] for p in packs])
    if len(packs)==1:
        decision.update(status="use_full_pack",reason="single_pack_sufficient",
                        pack_count=1, span_count=coverage_spans)
        return decision,None
    total_est=sum(Decimal(p[0]["estimated_usd"]) for p in packs)
    total_reserved=sum(Decimal(p[0]["reserved_usd"]) for p in packs)
    quote = {"schema_version":"multi-pack-cost-estimate/v1",
             "pricing_sha256":price_sha,"model_id":MODEL_ID,
             "catalog_version_id":model["catalog_version_id"],
             "pack_count":len(packs),"estimated_usd":_usd(total_est),
             "reserved_usd":_usd(total_reserved),
             "budget_cap_usd":config["budget_cap_usd"],
             "assumptions":"per-pack UTF8 x2 + wrapper/reserves; 50% expected output; 120% max-output contingency",
             "requires_new_cost_consent":True,
             "is_provider_price_guarantee":False}
    decision.update(pack_count=len(packs),span_count=coverage_spans,
                    fragment_count=len(fragments),total_payload_bytes=total_bytes,
                    quote=quote, pricing_sha256=price_sha)
    if total_reserved > Decimal(config["budget_cap_usd"]):
        decision.update(status="over_budget",reason="reserved_cost_exceeds_user_limit")
        return decision,None
    plan = {"schema_version":VERSION,"planner_version":PLANNER,
            "project_id":bundle["project_id"],"context_id":bundle["context_id"],
            "bundle_sha256":canonical_sha(bundle),
            "model_id":MODEL_ID,"catalog_version_id":model["catalog_version_id"],
            "catalog_sha256":model["catalog_sha256"],"pricing_sha256":price_sha,
            "config":config,"config_sha256":config_sha,"allow_partial":allow_partial,
            "coverage":{"fragment_count":len(fragments),"span_count":coverage_spans,
                        "covered_chars":sum(f["char_count"] for f in fragments),
                        "uncovered_required":0,"duplicate_characters":0,
                        "every_fragment_exactly_once":True},
            "quote":quote,
            "packs":[p[0] for p in packs],
            "pack_count":len(packs),
            "total_payload_bytes":total_bytes,
            "video_refs_excluded_count":len(bundle["video_analysis_refs"]),
            "excluded_source_count":len(bundle["excluded_sources"]),
            "tokenizer_check":"pending_exact_provider_preflight"}
    decision.update(status="fits_multi",reason=None,plan_sha256=canonical_sha(plan))
    return {**decision,"plan":plan},packs


def verify_multi_pack_plan(bundle, plan, packed):
    if not isinstance(plan,dict) or not isinstance(packed,list):
        raise IntakeError("multi_pack_plan_invalid")
    if (plan.get("schema_version")!=VERSION or plan.get("bundle_sha256")!=canonical_sha(bundle)
        or plan.get("pack_count")!=len(packed) or len(packed)<2
        or plan.get("packs") != [p[0] for p in packed]):
        raise IntakeError("multi_pack_plan_integrity")
    for part,raw in packed:
        verify_multi_pack_payload(part,raw)
    covered=_verify_coverage(bundle,[p[0] for p in packed])
    if plan.get("coverage")!={"fragment_count":len(bundle["fragments"]),
                               "span_count":covered,
                               "covered_chars":sum(f["char_count"] for f in bundle["fragments"]),
                               "uncovered_required":0,"duplicate_characters":0,
                               "every_fragment_exactly_once":True}:
        raise IntakeError("multi_pack_coverage_invalid")
    if sum(len(p[1]) for p in packed)!=plan.get("total_payload_bytes"):
        raise IntakeError("multi_pack_plan_bytes_mismatch")
    return True
