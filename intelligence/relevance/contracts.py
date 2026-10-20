"""Stable, provider-agnostic data contracts for project taxonomy and SRE pipelines."""
from __future__ import annotations
from collections.abc import Mapping
from math import isfinite
from uuid import UUID

class ContractError(ValueError):
    """Rejected input or model output. Never silently convert this to 'uncertain'."""

class InvalidModelOutput(ContractError):
    """Provider returned an invalid or incomplete classification payload."""

DECISIONS = frozenset(("match", "no_match", "uncertain"))
RELATIONS = frozenset(("direct", "problem_related", "adjacent"))
ORIGINS = frozenset(("explicit", "inferred", "user_added"))
STAGE_TYPES = frozenset(("embedding_retrieval", "llm_classifier"))
RUN_WHEN = frozenset(("always", "on_uncertain"))
EXAMPLE_KINDS = frozenset(("illustrative", "verified_video"))
EVIDENCE_SOURCES = frozenset(("title", "description", "transcript", "scene", "hashtag"))

def _object(value, label):
    if not isinstance(value, dict):
        raise ContractError(label + " must be an object")
    return value

def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractError(label + " must be nonempty text")
    return value.strip()

def _uuid(value, label):
    try:
        return str(UUID(_text(value, label)))
    except (ValueError, AttributeError) as exc:
        raise ContractError(label + " must be a UUID") from exc

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

def _examples(raw, rules):
    """Carry example kind explicitly. Old plain-text examples are illustrative only."""
    if raw is None:
        raw = {
          "positive":[{"text":x,"example_kind":"illustrative"} for x in rules["positive_examples"]],
          "negative":[{"text":x,"example_kind":"illustrative"} for x in rules["negative_examples"]]
        }
    raw = _object(raw, "examples")
    if set(raw) != {"positive","negative"}:
        raise ContractError("examples must contain positive and negative")
    cleaned = {}
    for polarity in ("positive","negative"):
        entries = raw[polarity]
        if not isinstance(entries,list):
            raise ContractError("examples must be lists")
        result = []
        for e in entries:
            e = _object(e, "example")
            kind = e.get("example_kind")
            if kind not in EXAMPLE_KINDS:
                raise ContractError("Unknown example_kind")
            video_id = e.get("video_id")
            if kind == "verified_video" and (type(video_id) is not int or video_id <= 0):
                raise ContractError("Verified video example requires positive video_id")
            if kind == "illustrative" and video_id is not None:
                raise ContractError("Illustrative example must not reference video")
            result.append({"text":_text(e.get("text"),"example.text"),
                           "example_kind":kind,"video_id":video_id})
        cleaned[polarity]=result
    return cleaned

def validate_category(category: dict) -> dict:
    data = _object(category, "category")
    key = _uuid(data.get("category_key"), "category_key")
    relation = data.get("relation_type")
    if relation not in RELATIONS:
        raise ContractError("Unsupported relation_type (use problem_related, not problem)")
    origin = data.get("origin")
    if origin not in ORIGINS:
        raise ContractError("origin must be explicit/inferred/user_added")
    rules = _object(data.get("rules"), "rules")
    for field in ("include", "exclude", "positive_examples", "negative_examples"):
        items = rules.get(field)
        if not isinstance(items, list) or any(not isinstance(x,str) or not x.strip() for x in items):
            raise ContractError(field + " must be a list of nonempty texts")
    related = data.get("related_fact_ids", [])
    if not isinstance(related,list):
        raise ContractError("related_fact_ids must be a list")
    related = [_uuid(x,"related_fact_id") for x in related]
    if len(set(related)) != len(related):
        raise ContractError("Duplicate related_fact_id")
    independent = data.get("independent",False)
    if type(independent) is not bool or (not related and not independent):
        raise ContractError("A topic needs approved fact links or explicit independent=true")
    if independent and related:
        raise ContractError("An independent topic cannot have linked facts")
    return {
        "category_key":key,
        "name":_text(data.get("name"),"name"),
        "relation_type":relation,
        "origin":origin,
        "definition":_text(data.get("definition"),"definition"),
        "rules":{k:list(rules[k]) for k in ("include","exclude","positive_examples","negative_examples")},
        "related_fact_ids":related,
        "independent":independent,
        "examples":_examples(data.get("examples"),rules)
    }

def validate_taxonomy(categories: list[dict]) -> list[dict]:
    if not isinstance(categories,list) or not categories:
        raise ContractError("Taxonomy requires at least one category")
    clean = [validate_category(c) for c in categories]
    keys = [c["category_key"] for c in clean]
    if len(set(keys)) != len(keys):
        raise ContractError("Duplicate category_key")
    return clean

def validate_pipeline(config: dict) -> dict:
    payload = _object(config,"pipeline")
    stages = payload.get("stages")
    if not isinstance(stages,list) or not stages:
        raise ContractError("Pipeline must contain stages")
    seen=set()
    classifier_seen=False
    active=[]
    for item in stages:
        stage = _object(item,"stage")
        ident = _text(stage.get("id"),"stage.id")
        if ident in seen:
            raise ContractError("Duplicate stage id")
        seen.add(ident)
        typ = stage.get("type")
        if typ not in STAGE_TYPES:
            raise ContractError("Unsupported stage type")
        if type(stage.get("enabled",True)) is not bool:
            raise ContractError("stage.enabled must be boolean")
        if not stage.get("enabled",True):
            continue
        _text(stage.get("model_ref"),"stage.model_ref")
        when = stage.get("run_when","always")
        if when not in RUN_WHEN:
            raise ContractError("Unknown run_when")
        if not active and when != "always":
            raise ContractError("First active stage must run always")
        if when == "on_uncertain" and not classifier_seen:
            raise ContractError("on_uncertain requires a preceding classifier")
        if typ == "embedding_retrieval":
            if classifier_seen or when != "always":
                raise ContractError("Embedding retrieval must precede classifiers and run always")
            settings=_object(stage.get("settings",{}),"embedding settings")
            if "top_k" in settings and (type(settings["top_k"]) is not int or settings["top_k"] < 1):
                raise ContractError("top_k must be a positive integer")
            if type(settings.get("allow_candidate_drop",False)) is not bool:
                raise ContractError("allow_candidate_drop must be bool")
            if settings.get("allow_candidate_drop",False):
                calibration=_object(settings.get("calibration"),"calibration")
                _text(calibration.get("dataset_id"),"calibration.dataset_id")
                _text(calibration.get("taxonomy_version"),"calibration.taxonomy_version")
                if calibration.get("model_ref") != stage["model_ref"]:
                    raise ContractError("Calibrated model_ref does not match stage")
                observed=calibration.get("observed_recall")
                if type(observed) not in (int,float) or not isfinite(observed) or not 0 <= observed <= 1:
                    raise ContractError("Calibration requires measured observed_recall")
                threshold=settings.get("min_similarity",-1)
                if type(threshold) not in (int,float) or not isfinite(threshold) or not -1 <= threshold <= 1:
                    raise ContractError("Invalid min_similarity")
        else:
            classifier_seen=True
        active.append(stage)
    if not classifier_seen:
        raise ContractError("At least one active classifier is required")
    return {"stages":[dict(s) for s in stages]}

def _source_text(video: dict, evidence: dict) -> str:
    source=evidence["source"]
    if source in ("title","description"):
        return video.get(source) if isinstance(video.get(source),str) else ""
    if source == "hashtag":
        tags=video.get("hashtags",[])
        return " ".join(tags) if isinstance(tags,list) and all(isinstance(x,str) for x in tags) else ""
    if source == "transcript":
        transcript=video.get("transcript","")
        if isinstance(transcript,str):
            return transcript
        if isinstance(transcript,list) and all(isinstance(x,dict) for x in transcript):
            if "index" in evidence:
                i=evidence["index"]
                return transcript[i].get("text","") if type(i) is int and 0<=i<len(transcript) else ""
            return " ".join(str(x.get("text","")) for x in transcript)
    if source == "scene":
        scenes=video.get("scenes",[])
        i=evidence.get("index")
        if isinstance(scenes,list) and type(i) is int and 0<=i<len(scenes) and isinstance(scenes[i],dict):
            return scenes[i].get("text","")
    return ""

def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())

def validate_evidence(video: dict, raw: list) -> list[dict]:
    if not isinstance(raw,list):
        raise InvalidModelOutput("Evidence must be an array")
    checked=[]
    for item in raw:
        if not isinstance(item,dict) or item.get("source") not in EVIDENCE_SOURCES:
            raise InvalidModelOutput("Evidence must specify a valid source")
        quote=item.get("quote")
        if not isinstance(quote,str) or not quote.strip():
            raise InvalidModelOutput("Evidence quote must be nonempty")
        if item["source"] == "scene" and type(item.get("index")) is not int:
            raise InvalidModelOutput("Scene evidence requires a scene index")
        for field in ("start_ms","end_ms"):
            if field in item and (type(item[field]) is not int or item[field] < 0):
                raise InvalidModelOutput("Evidence timestamps must be nonnegative integers")
        if "end_ms" in item and item["end_ms"] <= item.get("start_ms",0):
            raise InvalidModelOutput("Invalid evidence time interval")
        source=item["source"]
        timed=("start_ms" in item or "end_ms" in item)
        if timed and not ("start_ms" in item and "end_ms" in item):
            raise InvalidModelOutput("Evidence time spans require both start_ms and end_ms")
        if source not in ("scene","transcript") and (timed or "index" in item):
            raise InvalidModelOutput("Metadata evidence cannot have segment indices or timecodes")
        segments=video.get("scenes" if source=="scene" else "transcript")
        if source=="transcript" and isinstance(segments,str) and (timed or "index" in item):
            raise InvalidModelOutput("Plain-text transcript has no verified time offsets")
        if timed:
            index=item.get("index")
            if type(index) is not int or not isinstance(segments,list) or not (0<=index<len(segments)):
                raise InvalidModelOutput("Timed evidence requires a valid source segment index")
            segment=segments[index]
            if not isinstance(segment,dict) or type(segment.get("start_ms")) is not int or type(segment.get("end_ms")) is not int:
                raise InvalidModelOutput("Timed evidence source has no verified timestamps")
            if item["start_ms"]<segment["start_ms"] or item["end_ms"]>segment["end_ms"]:
                raise InvalidModelOutput("Evidence timecodes must fit the source segment")
        text=_source_text(video,item)
        if not isinstance(text,str) or _normalize_text(quote) not in _normalize_text(text):
            raise InvalidModelOutput("Evidence quote was not found in the selected content source")
        checked.append({k:item[k] for k in ("source","quote","index","start_ms","end_ms") if k in item})
    return checked

def validate_labels(raw: Mapping, category_ids: list[str], video: dict) -> dict:
    if not isinstance(raw,Mapping) or set(raw) != set(category_ids):
        raise InvalidModelOutput("Classifier must return exactly one result for every requested category")
    result={}
    for key in category_ids:
        value=raw[key]
        if not isinstance(value,dict):
            raise InvalidModelOutput("Category label must be an object")
        decision=value.get("decision")
        if decision not in DECISIONS:
            raise InvalidModelOutput("Decision must be match/no_match/uncertain")
        reason=value.get("reason")
        if not isinstance(reason,str) or not reason.strip():
            raise InvalidModelOutput("Reason must be nonempty for every decision")
        evidence=validate_evidence(video,value.get("evidence",[]))
        if decision == "match" and not evidence:
            raise InvalidModelOutput("Match requires source-verified evidence")
        result[key]={"decision":decision,"reason":reason.strip(),"evidence":evidence}
    return result
