"""Strict raw LLM contract validation independent of optional jsonschema.

Implements every field of docs/SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json,
then enforces batch membership, facet keys, angle and policy. Pure Python.
"""
from __future__ import annotations

import re

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

_ID = re.compile(r"[a-z][a-z0-9_-]{1,39}\Z")
_FACET = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_LANG = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
_ANGLES = {"direct", "problem", "solution", "situation", "format",
           "comparison", "experience", "critical", "other"}
_MODES = {"keyword", "hashtag"}
_STANCES = {"support", "challenge", "neutral"}
_PROFILE_FIELDS = {"core_activity", "audience", "problem", "environment",
                   "mandatory_constraints", "unknowns"}
_FACET_FIELDS = {"facet_key", "angle", "intent", "anchor_terms", "sketches", "lexical_hints"}
_QUERY_FIELDS = {"facet_key", "text", "query_mode", "angle", "language",
                 "research_stance", "rationale"}
_HYP_FIELDS = {"hypothesis_id", "semantic_profile", "facets", "queries", "no_viable_reason"}


def _object(obj, fields, name):
    if type(obj) is not dict or set(obj) != fields:
        raise PipelineError("Invalid " + name + " fields in generated JSON", 422)


def _string(obj, name, *, maximum, minimum=0, pattern=None):
    if type(obj) is not str or not minimum <= len(obj) <= maximum or (
        pattern is not None and pattern.fullmatch(obj) is None
    ):
        raise PipelineError("Invalid generated " + name, 422)
    return obj


def _list(obj, name, *, maximum, minimum=0):
    if type(obj) is not list or not minimum <= len(obj) <= maximum:
        raise PipelineError("Invalid generated " + name, 422)
    return obj


def _strings(obj, name, maximum_items, maximum_chars, *, minimum_items=0, minimum_chars=1):
    _list(obj, name, maximum=maximum_items, minimum=minimum_items)
    for value in obj:
        _string(value, name, maximum=maximum_chars, minimum=minimum_chars)


def validate_raw_batch(raw: dict, contexts: list[dict], policy: dict) -> dict:
    """Reject structurally invalid and cross-hypothesis model data.

    Never accepts a partial list of hypotheses or model-issued project IDs.
    Semantic relevance and query MMR belong to Query Validation, not here.
    """
    _object(raw, {"hypotheses"}, "raw batch")
    items = _list(raw["hypotheses"], "hypotheses", maximum=5, minimum=1)
    expected = {c["hypothesis"]["id"]: c for c in contexts}
    if len(expected) != len(contexts) or len(items) != len(contexts):
        raise PipelineError("Batch does not cover each original hypothesis once", 422)
    seen = set()
    total = 0
    for item in items:
        _object(item, _HYP_FIELDS, "hypothesis")
        ident = _string(item["hypothesis_id"], "hypothesis_id",
                        maximum=40, minimum=2, pattern=_ID)
        if ident in seen or ident not in expected:
            raise PipelineError("Unknown or repeated generated hypothesis ID", 422)
        seen.add(ident)
        profile = item["semantic_profile"]
        _object(profile, _PROFILE_FIELDS, "semantic profile")
        for name in ("core_activity", "audience", "problem", "environment"):
            _string(profile[name], name, maximum=200)
        _strings(profile["mandatory_constraints"], "mandatory_constraints", 12, 160)
        _strings(profile["unknowns"], "unknowns", 12, 160)
        facets = _list(item["facets"], "facets", maximum=6)
        keys = {}
        for facet in facets:
            _object(facet, _FACET_FIELDS, "facet")
            key = _string(facet["facet_key"], "facet_key", maximum=40, minimum=1, pattern=_FACET)
            if key in keys:
                raise PipelineError("Duplicate facet_key", 422)
            if facet["angle"] not in _ANGLES:
                raise PipelineError("Unknown search angle", 422)
            _string(facet["intent"], "intent", maximum=240, minimum=1)
            _strings(facet["anchor_terms"], "anchor_terms", 4, 80, minimum_items=1)
            _strings(facet["sketches"], "sketches", 2, 160)
            _strings(facet["lexical_hints"], "lexical_hints", 4, 90)
            keys[key] = facet["angle"]
        queries = _list(item["queries"], "queries",
                        maximum=min(16, policy["max_candidates_per_hypothesis"]))
        total += len(queries)
        for query in queries:
            _object(query, _QUERY_FIELDS, "query")
            key = _string(query["facet_key"], "query facet", maximum=40,
                          minimum=1, pattern=_FACET)
            if key not in keys or query["angle"] != keys[key]:
                raise PipelineError("Query facet missing or mismatched angle", 422)
            _string(query["text"], "query text", maximum=120, minimum=1)
            if not query["text"].strip():
                raise PipelineError("Blank search query", 422)
            _string(query["rationale"], "rationale", maximum=280, minimum=1)
            lang = _string(query["language"], "language", maximum=6,
                           minimum=2, pattern=_LANG)
            if lang not in policy["languages"]:
                raise PipelineError("Query language not in approved policy", 422)
            if query["query_mode"] not in policy["allowed_modes"] or query["query_mode"] not in _MODES:
                raise PipelineError("Query mode not permitted", 422)
            if query["research_stance"] not in _STANCES:
                raise PipelineError("Unknown research stance", 422)
        reason = item["no_viable_reason"]
        if queries:
            if reason is not None:
                raise PipelineError("Generated hypothesis must not have no_viable_reason", 422)
        else:
            _string(reason, "no viable reason", maximum=280, minimum=1)
            if not reason.strip():
                raise PipelineError("No viable reason cannot be blank", 422)
    if seen != set(expected):
        raise PipelineError("Incomplete generated hypothesis coverage", 422)
    if total > policy["max_total_candidates"]:
        raise PipelineError("Generation exceeded total candidates budget", 422)
    return raw
