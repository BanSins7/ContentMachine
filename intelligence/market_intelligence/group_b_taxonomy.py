"""Marketing-owned B8 taxonomy matching. No QDP/Group B database access.

A deployment-pinned, explicitly configured catalog is read-only.
The original copied Group B TopicRegistry algorithms validate hierarchy,
merges and duplicate identity. SHA pin is integrity, not operator approval.
Unknown/ambiguous proposals never become a fabricated taxonomy ID.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from intelligence import config
from ._copied.group_b.topic_registry import (
    TopicNode, make_topic_registry, normalize_label, TopicRegistryError,
)

HEX=re.compile(r"[a-f0-9]{64}\Z")
SCHEMA="mi-b8-taxonomy/v1"
LIMIT=1_000_000

def _catalog():
    raw=config.setting("CM_MARKET_B_TAXONOMY_JSON","")
    pin=config.setting("CM_MARKET_B_TAXONOMY_SHA256","").strip()
    if not raw and not pin:return None,"taxonomy_not_configured"
    if (type(raw) is not str or len(raw.encode("utf-8"))>LIMIT
        or type(pin) is not str or HEX.fullmatch(pin) is None):
        return None,"taxonomy_pin_missing_or_size_limit"
    # SHA of exact bytes prevents silent substitution (not an IAM grant).
    if sha256(raw.encode("utf-8")).hexdigest()!=pin:
        return None,"taxonomy_sha256_mismatch"
    try:
        document=json.loads(raw,parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
        if type(document) is not dict or set(document)!={"schema_version","categories","topics","subtopics"}:
            return None,"taxonomy_schema_invalid"
        if document["schema_version"]!=SCHEMA:
            return None,"taxonomy_schema_incompatible"
        groups=[]
        for level in ("categories","topics","subtopics"):
            rows=document[level]
            if type(rows) is not list:
                return None,"taxonomy_rows_invalid"
            nodes=[]
            for row in rows:
                if type(row) is not dict or set(row)!={"id","slug","name","parent_id","merged_into_id"}:
                    return None,"taxonomy_node_invalid"
                nodes.append(TopicNode(**row))
            groups.append(nodes)
        return make_topic_registry(categories=groups[0],topics=groups[1],subtopics=groups[2]),None
    except (TypeError,ValueError,OverflowError,TopicRegistryError):
        return None,"taxonomy_validation_failed"

def resolve_topic(labels:dict,evidence_refs:list[str],*,catalog=None)->dict:
    """Map ONLY a model's explicit hierarchical labels into pinned IDs."""
    if not isinstance(labels,dict) or not isinstance(evidence_refs,list):
        raise ValueError("invalid_b8_labels")
    fields=("category_label","topic_label","subtopic_label")
    if any(labels.get(k) is not None and
           (type(labels[k]) is not str or not 1<=len(labels[k])<=160)
           for k in fields):
        raise ValueError("invalid_b8_label")
    result={"state":"unavailable","reason":"no_observed_topic_labels",
        "category_label":labels.get("category_label"),
        "topic_label":labels.get("topic_label"),
        "subtopic_label":labels.get("subtopic_label"),
        "category_id":None,"topic_id":None,"subtopic_id":None,
        "evidence_frame_refs":list(evidence_refs),
        "catalog_sha256":None,
        "mi_approved_topic_match":False}
    if not evidence_refs or not labels.get("category_label") or not labels.get("topic_label"):
        return result
    if catalog is None:
        catalog,reason=_catalog()
        if catalog is None:
            return {**result,"state":"observed_unmapped","reason":reason}
    if not catalog.verify_digest():
        return {**result,"state":"observed_unmapped","reason":"catalog_digest_invalid"}
    result["catalog_sha256"]=catalog.snapshot_sha256
    try:
        category,_=catalog.resolve("category",labels["category_label"])
        if category is None:
            raise ValueError("unrecognized_category")
        topic,_=catalog.resolve("topic",labels["topic_label"],category)
        if topic is None:
            raise ValueError("unrecognized_topic")
        subtopic=None
        if labels.get("subtopic_label"):
            subtopic,_=catalog.resolve("subtopic",labels["subtopic_label"],topic)
            if subtopic is None:
                raise ValueError("unrecognized_subtopic")
    except (ValueError,TopicRegistryError) as exc:
        return {**result,"state":"observed_unmapped",
                "reason":getattr(exc,"reason",str(exc))[:90]}
    return {**result,"state":"catalog_matched",
        "reason":None,"category_id":category,"topic_id":topic,
        "subtopic_id":subtopic,"mi_approved_topic_match":True}
