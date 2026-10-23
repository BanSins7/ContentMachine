"""V1 execution, model, money and Analytics boundary contracts; no I/O.

These contracts describe immutable values. A validated quote is NOT a spending
consent; a validated consent is NOT permission to execute without DB auth and
transactional reservation; an outbox event is NOT automatically delivered.
"""
from __future__ import annotations

from .contracts import ContractError, validate_pipeline
from .v1_contracts import (
    CONTRACT_VERSIONS, EVENT_TYPES, SREContractError, _bounded_list, _unique_strings,
    _version, canonical_uuid, code, decimal_usd, nonempty, positive, project_id,
    record, reject, sha256_digest, utc_time,
)


def _key_list(values, path, *, minimum=0):
    keys = _unique_strings(_bounded_list(values, path, minimum=minimum, maximum=48),
                           path, maximum=48)
    return [canonical_uuid(v, path) for v in keys]


def validate_pipeline_snapshot(payload):
    record(payload, "pipeline", required=("schema_version", "project_id",
        "pipeline_config_id", "taxonomy_version_id", "config", "models"))
    _version(payload, "pipeline")
    pid = project_id(payload["project_id"])
    conf_id = positive(payload["pipeline_config_id"], "pipeline_config_id")
    taxonomy = positive(payload["taxonomy_version_id"], "taxonomy_version_id")
    try:
        config = validate_pipeline(payload["config"])
    except (ContractError, ValueError, TypeError) as exc:
        raise SREContractError("invalid_contract", "config", str(exc)) from exc
    models = payload["models"]
    if type(models) is not dict:
        reject("models", "expected a mapping from active model_ref to pinned model")
    refs = {s["model_ref"] for s in config["stages"] if s.get("enabled", True)}
    if set(models) != refs:
        reject("models", "every active stage must have exactly one pinned model")
    result = {}
    for ref, value in models.items():
        ref = code(ref, "models.model_ref")
        path = f"models.{ref}"
        record(value, path, required=("provider", "model_id", "model_version",
               "catalog_snapshot_id", "catalog_sha256", "prompt_sha256",
               "output_schema_sha256", "input_usd_per_million",
               "output_usd_per_million", "capability"))
        capability = value["capability"]
        if capability not in ("embedding_retrieval", "llm_classifier"):
            reject(f"{path}.capability", "unsupported model capability")
        if any(s["type"] != capability for s in config["stages"]
               if s.get("enabled", True) and s["model_ref"] == ref):
            reject(f"{path}.capability", "model capability mismatches stage type")
        result[ref] = {
            "provider": code(value["provider"], f"{path}.provider"),
            "model_id": nonempty(value["model_id"], f"{path}.model_id", limit=200),
            "model_version": nonempty(value["model_version"], f"{path}.model_version", limit=200),
            "catalog_snapshot_id": positive(value["catalog_snapshot_id"], f"{path}.catalog_snapshot_id"),
            "catalog_sha256": sha256_digest(value["catalog_sha256"], f"{path}.catalog_sha256"),
            "prompt_sha256": sha256_digest(value["prompt_sha256"], f"{path}.prompt_sha256"),
            "output_schema_sha256": sha256_digest(value["output_schema_sha256"], f"{path}.output_schema_sha256"),
            "input_usd_per_million": value["input_usd_per_million"],
            "output_usd_per_million": value["output_usd_per_million"],
            "capability": capability,
        }
        decimal_usd(value["input_usd_per_million"], f"{path}.input_usd_per_million")
        decimal_usd(value["output_usd_per_million"], f"{path}.output_usd_per_million")
    return {"schema_version": CONTRACT_VERSIONS["pipeline"], "project_id": pid,
            "pipeline_config_id": conf_id, "taxonomy_version_id": taxonomy,
            "config": config, "models": result}


def validate_run_plan(payload):
    record(payload, "run", required=("schema_version", "project_id", "run_id",
        "publication_id", "taxonomy_version_id", "taxonomy_sha256",
        "pipeline_config_id", "pipeline_sha256", "idempotency_key",
        "state", "items"))
    _version(payload, "run")
    pid = project_id(payload["project_id"])
    rid = canonical_uuid(payload["run_id"], "run_id")
    pub = canonical_uuid(payload["publication_id"], "publication_id")
    taxonomy_id = positive(payload["taxonomy_version_id"], "taxonomy_version_id")
    tax_hash = sha256_digest(payload["taxonomy_sha256"], "taxonomy_sha256")
    pipeline_id = positive(payload["pipeline_config_id"], "pipeline_config_id")
    pipeline_hash = sha256_digest(payload["pipeline_sha256"], "pipeline_sha256")
    idem = nonempty(payload["idempotency_key"], "idempotency_key", limit=120)
    if not all(c.isascii() and (c.isalnum() or c in "._-") for c in idem):
        reject("idempotency_key", "expected ASCII letters, numbers, dot, underscore or hyphen")
    if payload["state"] != "awaiting_authorization":
        reject("state", "a planned run must start awaiting authorization")
    items = []
    seen = set()
    for i, raw in enumerate(_bounded_list(payload["items"], "items", minimum=1, maximum=10000)):
        path = f"items[{i}]"
        record(raw, path, required=("video_id", "content_version_id",
                                    "content_sha256", "hypothesis_keys"))
        video = positive(raw["video_id"], f"{path}.video_id")
        revision = positive(raw["content_version_id"], f"{path}.content_version_id")
        digest = sha256_digest(raw["content_sha256"], f"{path}.content_sha256")
        keys = _key_list(raw["hypothesis_keys"], f"{path}.hypothesis_keys", minimum=1)
        if video in seen:
            reject(path, "video may appear only once in a single run")
        seen.add(video)
        items.append({"video_id": video, "content_version_id": revision,
                      "content_sha256": digest, "hypothesis_keys": keys})
    return {"schema_version": CONTRACT_VERSIONS["run"], "project_id": pid,
            "run_id": rid, "publication_id": pub,
            "taxonomy_version_id": taxonomy_id, "taxonomy_sha256": tax_hash,
            "pipeline_config_id": pipeline_id, "pipeline_sha256": pipeline_hash,
            "idempotency_key": idem, "state": "awaiting_authorization",
            "items": items}


def validate_cost_quote(payload):
    record(payload, "quote", required=("schema_version", "project_id", "run_id",
        "quote_id", "model_catalog_sha256", "request_sha256",
        "issued_at", "expires_at", "low_usd", "expected_usd", "high_usd"))
    _version(payload, "quote")
    pid = project_id(payload["project_id"])
    run_id = canonical_uuid(payload["run_id"], "run_id")
    quote_id = canonical_uuid(payload["quote_id"], "quote_id")
    catalog_hash = sha256_digest(payload["model_catalog_sha256"], "model_catalog_sha256")
    req_hash = sha256_digest(payload["request_sha256"], "request_sha256")
    issued = utc_time(payload["issued_at"], "issued_at")
    expires = utc_time(payload["expires_at"], "expires_at")
    if expires <= issued:
        reject("expires_at", "quote expiry must exceed issue time")
    low, expected, high = (
        decimal_usd(payload[k], k) for k in ("low_usd", "expected_usd", "high_usd"))
    if not low <= expected <= high or high <= 0:
        reject("quote", "expected 0 <= low <= expected <= high and high > 0")
    return {"schema_version": CONTRACT_VERSIONS["quote"], "project_id": pid,
            "run_id": run_id, "quote_id": quote_id, "model_catalog_sha256": catalog_hash,
            "request_sha256": req_hash, "issued_at": payload["issued_at"],
            "expires_at": payload["expires_at"], "low_usd": payload["low_usd"],
            "expected_usd": payload["expected_usd"], "high_usd": payload["high_usd"]}


def validate_spending_consent(payload, quote):
    """Consent must correspond to the exact quote, model and run, no implicit grants."""
    q = validate_cost_quote(quote)
    record(payload, "consent", required=("schema_version", "project_id", "run_id",
        "quote_id", "consent_id", "authorized_usd", "approved_at",
        "expires_at", "revoked_at"))
    _version(payload, "consent")
    pid = project_id(payload["project_id"])
    rid = canonical_uuid(payload["run_id"], "run_id")
    qid = canonical_uuid(payload["quote_id"], "quote_id")
    cid = canonical_uuid(payload["consent_id"], "consent_id")
    if pid != q["project_id"] or rid != q["run_id"] or qid != q["quote_id"]:
        reject("consent", "consent must belong to the exact project/run/quote",
               "snapshot_mismatch")
    money = decimal_usd(payload["authorized_usd"], "authorized_usd", strictly_positive=True)
    if money < decimal_usd(q["high_usd"], "quote.high_usd"):
        reject("authorized_usd", "authorization cannot be below full quote high estimate")
    approved = utc_time(payload["approved_at"], "approved_at")
    expires = utc_time(payload["expires_at"], "expires_at")
    if approved < utc_time(q["issued_at"], "quote.issued_at"):
        reject("approved_at", "consent predates quote")
    if approved >= utc_time(q["expires_at"], "quote.expires_at"):
        reject("approved_at", "quote expired before consent")
    if expires <= approved:
        reject("expires_at", "consent expiry must exceed approval")
    revoked = payload["revoked_at"]
    if revoked is not None and utc_time(revoked, "revoked_at") < approved:
        reject("revoked_at", "revocation cannot predate approval")
    return {"schema_version": CONTRACT_VERSIONS["consent"],
            "project_id": pid, "run_id": rid, "quote_id": qid,
            "consent_id": cid, "authorized_usd": payload["authorized_usd"],
            "approved_at": payload["approved_at"], "expires_at": payload["expires_at"],
            "revoked_at": revoked}


def validate_spending_position(authorized_usd, spent_usd, reserved_usd):
    """SQL must enforce same invariant atomically under a row lock."""
    authorized = decimal_usd(authorized_usd, "authorized_usd")
    spent = decimal_usd(spent_usd, "spent_usd")
    reserved = decimal_usd(reserved_usd, "reserved_usd")
    if spent + reserved > authorized:
        reject("budget", "spent + reserved exceeds explicitly authorized cap",
               "budget_exceeded")
    return {"authorized_usd": authorized_usd, "spent_usd": spent_usd,
            "reserved_usd": reserved_usd}


def validate_analytics_event(payload):
    record(payload, "event", required=("schema_version", "event_id",
        "event_type", "project_id", "run_id", "publication_id",
        "taxonomy_version_id", "occurred_at"),
        optional=("video_id", "content_version_id", "source_policy_version_id",
                  "result_sha256", "decisions", "reason"))
    _version(payload, "event")
    event_id = canonical_uuid(payload["event_id"], "event_id")
    typ = payload["event_type"]
    if typ not in EVENT_TYPES:
        reject("event_type", "unsupported SRE event")
    pid = project_id(payload["project_id"])
    run_id = canonical_uuid(payload["run_id"], "run_id")
    pub = canonical_uuid(payload["publication_id"], "publication_id")
    taxid = positive(payload["taxonomy_version_id"], "taxonomy_version_id")
    utc_time(payload["occurred_at"], "occurred_at")
    result = {"schema_version": CONTRACT_VERSIONS["event"], "event_id": event_id,
              "event_type": typ, "project_id": pid, "run_id": run_id,
              "publication_id": pub, "taxonomy_version_id": taxid,
              "occurred_at": payload["occurred_at"]}
    item_event = typ == "sre.classification_item_completed"
    if item_event:
        required = ("video_id", "content_version_id", "source_policy_version_id",
                    "result_sha256", "decisions")
        if any(k not in payload for k in required):
            reject("event", "completed item event lacks mandatory lineage/decisions")
    if typ == "sre.classification_run_completed" and any(
        k in payload for k in ("video_id", "content_version_id", "decisions")
    ):
        reject("event", "run summary must not pretend to be item results")
    for k in ("video_id", "content_version_id", "source_policy_version_id"):
        if k in payload:
            result[k] = positive(payload[k], k)
    if "result_sha256" in payload:
        result["result_sha256"] = sha256_digest(payload["result_sha256"], "result_sha256")
    if "reason" in payload:
        result["reason"] = nonempty(payload["reason"], "reason", limit=500)
    if typ in ("sre.classification_invalidated", "sre.rights_revoked") and "reason" not in payload:
        reject("reason", "invalidation must include reason")
    if "decisions" in payload:
        if not item_event:
            reject("decisions", "decisions permitted only on item completion")
        decisions = record(payload["decisions"], "decisions",
                           required=("match", "no_match", "uncertain", "not_evaluated"))
        clean = {k: _key_list(v, f"decisions.{k}") for k, v in decisions.items()}
        all_keys = [v for values in clean.values() for v in values]
        if len(all_keys) != len(set(all_keys)) or not all_keys:
            reject("decisions", "overlapping or empty hypothesis decisions")
        result["decisions"] = clean
    return result
