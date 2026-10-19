"""Deterministic boundaries for dynamic per-project taxonomy and configurable pipelines."""
from __future__ import annotations
from collections.abc import Mapping

class ContractError(ValueError):
    pass

DECISIONS = frozenset(("match", "no_match", "uncertain"))
RELATIONS = frozenset(("direct", "problem", "adjacent"))
STAGE_TYPES = frozenset(("embedding_retrieval", "llm_classifier"))
RUN_WHEN = frozenset(("always", "on_uncertain"))

def _object(value, label):
    if not isinstance(value, dict):
        raise ContractError(label + " must be an object")
    return value

def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " must be nonempty text")
    return value.strip()

def validate_profile(payload: dict) -> dict:
    data = _object(payload, "profile")
    if data.get("niche_code") != "health_fitness":
        raise ContractError("Only health_fitness is supported in v1")
    if data.get("research_goal", "maximize_views") != "maximize_views":
        raise ContractError("Research goal is fixed to maximize_views")
    return {
        "niche_code": "health_fitness",
        "research_goal": "maximize_views",
        "product_type": _text(data.get("product_type"), "product_type"),
        "functions_text": _text(data.get("functions_text"), "functions_text"),
        "problems_text": _text(data.get("problems_text"), "problems_text"),
    }

def validate_category(category: dict) -> dict:
    data = _object(category, "category")
    key = _text(data.get("category_key"), "category_key")
    relation = _text(data.get("relation_type"), "relation_type")
    if relation not in RELATIONS:
        raise ContractError("Unsupported relation_type")
    rules = _object(data.get("rules"), "rules")
    for field in ("include", "exclude", "positive_examples", "negative_examples"):
        items = rules.get(field)
        if not isinstance(items, list) or any(not isinstance(x, str) or not x.strip() for x in items):
            raise ContractError(field + " must be a list of nonempty texts")
    related = data.get("related_fact_ids", [])
    if not isinstance(related, list) or any(not isinstance(x, str) or not x for x in related):
        raise ContractError("related_fact_ids must be a list of identifiers")
    return {
        "category_key": key,
        "name": _text(data.get("name"), "name"),
        "relation_type": relation,
        "definition": _text(data.get("definition"), "definition"),
        "rules": {k: list(rules[k]) for k in ("include","exclude","positive_examples","negative_examples")},
        "related_fact_ids": related,
    }

def validate_taxonomy(categories: list[dict]) -> list[dict]:
    if not isinstance(categories, list) or not categories:
        raise ContractError("Taxonomy requires at least one category")
    clean = [validate_category(c) for c in categories]
    keys = [c["category_key"] for c in clean]
    if len(set(keys)) != len(keys):
        raise ContractError("Duplicate category_key")
    return clean

def validate_pipeline(config: dict) -> dict:
    payload = _object(config, "pipeline")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ContractError("Pipeline must contain stages")
    seen = set()
    has_classifier = False
    classifier_seen = False
    active = []
    for item in stages:
        stage = _object(item, "stage")
        stage_id = _text(stage.get("id"), "stage.id")
        if stage_id in seen:
            raise ContractError("Duplicate stage id")
        seen.add(stage_id)
        typ = _text(stage.get("type"), "stage.type")
        if typ not in STAGE_TYPES:
            raise ContractError("Unsupported stage type")
        if not isinstance(stage.get("enabled", True), bool):
            raise ContractError("stage.enabled must be boolean")
        if not stage.get("enabled", True):
            continue
        _text(stage.get("model_ref"), "stage.model_ref")
        when = stage.get("run_when", "always")
        if when not in RUN_WHEN:
            raise ContractError("Unknown run_when")
        if not active and when != "always":
            raise ContractError("First active stage must run always")
        if when == "on_uncertain" and not classifier_seen:
            raise ContractError("on_uncertain requires a preceding classifier")
        if typ == "embedding_retrieval":
            if classifier_seen:
                raise ContractError("Embedding retrieval must precede classifiers")
            if when != "always":
                raise ContractError("Embedding retrieval must run always")
            settings = _object(stage.get("settings", {}), "embedding settings")
            if "top_k" in settings and (type(settings["top_k"]) is not int or settings["top_k"] < 1):
                raise ContractError("top_k must be a positive integer")
            if settings.get("allow_candidate_drop", False):
                if settings.get("calibrated") is not True:
                    raise ContractError("Dropping candidates requires calibrated=true")
        else:
            classifier_seen = True
            has_classifier = True
        active.append(stage)
    if not has_classifier:
        raise ContractError("At least one active classifier is required")
    return {"stages": [dict(s) for s in stages]}

def validate_labels(raw: Mapping, category_ids: list[str]) -> dict:
    if not isinstance(raw, Mapping):
        raise ContractError("Classifier result must be a mapping")
    unknown = set(raw) - set(category_ids)
    if unknown:
        raise ContractError("Classifier returned unknown categories")
    result = {}
    for key in category_ids:
        value = raw.get(key)
        if value is None:
            result[key] = {"decision":"uncertain", "evidence":[]}
            continue
        _object(value, "label")
        decision = value.get("decision")
        if decision not in DECISIONS:
            raise ContractError("Classifier decision must be match/no_match/uncertain")
        evidence = value.get("evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(x, str) for x in evidence):
            raise ContractError("evidence must be a list of texts")
        result[key] = {"decision":decision, "evidence":evidence}
    return result
