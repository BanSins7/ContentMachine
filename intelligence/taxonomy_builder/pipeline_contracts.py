"""Strict Taxonomy Builder pipeline contracts; no LLM/model API calls.

OpenRouter advertises supported_parameters and reasoning.supported_efforts.
Those claims do not establish that temperature and explicit reasoning work
simultaneously. Such combinations require a verified allow-list, not guessing.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import hashlib
import json

class PipelineError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.http_status = status

ROLE_ORDER = ("fact_extraction", "taxonomy_generation", "taxonomy_critic")
REQUIRED = ROLE_ORDER[:2]
EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
PROMPTS = {
    "fact_extraction": "facts-v1",
    "taxonomy_generation": "taxonomy-v1",
    "taxonomy_critic": "critic-v1",
}

ALLOWED_PROMPT_VERSIONS = {
    "fact_extraction": frozenset({"facts-v1","facts-v2"}),
    "taxonomy_generation": frozenset({"taxonomy-v1","taxonomy-v2"}),
    "taxonomy_critic": frozenset({"critic-v1","critic-v2"}),
}

def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)

def sha256(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(field + " must be nonempty")
    return value.strip()

def _int(value, field, minimum=1):
    if type(value) is not int or value < minimum:
        raise PipelineError(field + " must be integer >= " + str(minimum))
    return value

def _decimal(value, field):
    if type(value) not in (int, float, str, Decimal):
        raise PipelineError(field + " must be numeric")
    try:
        number=Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PipelineError(field + " must be numeric") from exc
    if not number.is_finite():
        raise PipelineError(field + " must be finite")
    return number

def _object(value, field):
    if not isinstance(value, dict):
        raise PipelineError(field + " must be an object")
    return value

def normalize_capability(model: dict) -> dict:
    """Parse a *fresh server catalog record*, not user-editable flags."""
    model=_object(model, "model metadata")
    model_id=_text(model.get("id"), "id")
    params=model.get("supported_parameters")
    if not isinstance(params,list) or any(not isinstance(x,str) for x in params):
        raise PipelineError("Missing provider-supported parameter list")
    parameters=sorted(set(params))
    reasoning=model.get("reasoning") or {}
    if not isinstance(reasoning,dict):
        raise PipelineError("Invalid reasoning metadata")
    efforts=reasoning.get("supported_efforts",[])
    if not isinstance(efforts,list) or any(x not in EFFORTS for x in efforts):
        raise PipelineError("Invalid provider reasoning efforts")
    if efforts and "reasoning" not in parameters:
        raise PipelineError("Conflicting provider reasoning flags")
    top=model.get("top_provider") or {}
    if not isinstance(top,dict):
        raise PipelineError("Invalid provider limits")
    limit=top.get("max_completion_tokens")
    if limit is not None:
        _int(limit,"max_completion_tokens")
    context=model.get("context_length")
    if context is not None:
        _int(context,"context_length")
    # No undocumented reasoning-budget capability or combined sampling rules are inferred.
    compatibility=reasoning.get("temperature_compatible_efforts",[])
    if not isinstance(compatibility,list) or any(x not in efforts for x in compatibility):
        raise PipelineError("Invalid temperature-reasoning compatibility")
    modality=(model.get("architecture") or {}).get("output_modalities",[])
    if not isinstance(modality,list):
        raise PipelineError("Invalid output modalities")
    return {
      "model_id": model_id,
      "supported_parameters": parameters,
      "reasoning_efforts": list(dict.fromkeys(efforts)),
      "reasoning_mandatory": reasoning.get("mandatory") is True,
      "reasoning_default_enabled": reasoning.get("default_enabled") is True,
      "temperature_default_compatible": reasoning.get("temperature_compatible_default") is True,
      "reasoning_budget_limits": None,
      "temperature_compatible_efforts": list(dict.fromkeys(compatibility)),
      "supports_temperature": "temperature" in parameters,
      "supports_structured_outputs": ("structured_outputs" in parameters or
                                      "response_format" in parameters),
      "max_output_tokens": limit,
      "context_length": context,
      "text_output": "text" in modality,
    }

def validate_pipeline(config: dict, by_id: dict[int,dict]) -> tuple[dict,dict]:
    """Normalize config and return immutable per-stage catalog snapshots."""
    config=_object(config,"config")
    if set(config)!={"schema_version","stages"} or config["schema_version"]!=1:
        raise PipelineError("Expected pipeline schema_version 1 and stages only")
    stages=config["stages"]
    if not isinstance(stages,list) or len(stages) not in (2,3):
        raise PipelineError("Two required stages; optional critic is third")
    seen=[]
    normalized=[]
    snapshots={}
    for stage in stages:
        stage=_object(stage,"stage")
        role=stage.get("role")
        if role not in ROLE_ORDER or role in seen:
            raise PipelineError("Unknown or duplicate stage role")
        seen.append(role)
        enabled=stage.get("enabled",True)
        if type(enabled) is not bool:
            raise PipelineError("enabled must be boolean")
        if not enabled:
            if role!="taxonomy_critic":
                raise PipelineError("Cannot disable required LLM stages")
            if set(stage)-{"role","enabled"}:
                raise PipelineError("Disabled critic must have no unused model settings")
            normalized.append({"role":role,"enabled":False})
            continue
        allowed={"role","enabled","model_id","catalog_version_id",
                 "reasoning","temperature","max_output_tokens","prompt_version"}
        if set(stage)-allowed:
            raise PipelineError("Unknown stage settings")
        model_id=_text(stage.get("model_id"),"model_id")
        catalog_id=_int(stage.get("catalog_version_id"),"catalog_version_id")
        entry=by_id.get(catalog_id)
        if not entry or entry.get("model_id")!=model_id:
            raise PipelineError("Selected model catalog version is not available",409)
        cap=_object(entry.get("capabilities"),"capabilities")
        if not cap.get("text_output") or not cap.get("supports_structured_outputs"):
            raise PipelineError("Model needs text output and JSON response support")
        max_output=_int(stage.get("max_output_tokens"),"max_output_tokens",128)
        if cap.get("max_output_tokens") and max_output>cap["max_output_tokens"]:
            raise PipelineError("Output limit exceeds provider maximum")
        reasoning=_object(stage.get("reasoning"),"reasoning")
        mode=reasoning.get("mode")
        if mode=="default":
            if set(reasoning)!={"mode"}:
                raise PipelineError("Default reasoning cannot include extra parameters")
            selected={"mode":"default"}
        elif mode=="effort":
            if set(reasoning)!={"mode","effort"} or reasoning.get("effort") not in cap.get("reasoning_efforts",[]):
                raise PipelineError("Reasoning effort is unverified for this model")
            selected={"mode":"effort","effort":reasoning["effort"]}
        elif mode=="budget":
            limits=cap.get("reasoning_budget_limits")
            if set(reasoning)!={"mode","budget_tokens"} or not isinstance(limits,dict):
                raise PipelineError("Reasoning token budget is not verified for this model")
            count=_int(reasoning["budget_tokens"],"reasoning.budget_tokens")
            if not limits["min"]<=count<=limits["max"] or count>=max_output:
                raise PipelineError("Reasoning budget exceeds verified limits")
            selected={"mode":"budget","budget_tokens":count}
        else:
            raise PipelineError("Choose reasoning mode: default, effort or budget")
        temperature=stage.get("temperature")
        if temperature is not None:
            if not cap.get("supports_temperature"):
                raise PipelineError("This model does not support temperature")
            value=_decimal(temperature,"temperature")
            if not Decimal(0)<=value<=Decimal(2):
                raise PipelineError("Temperature must be between 0 and 2")
            if mode=="default" and (cap.get("reasoning_mandatory") or cap.get("reasoning_default_enabled")) and not cap.get("temperature_default_compatible"):
                raise PipelineError("Temperature with active default reasoning is not verified; omit temperature")
            if mode=="effort" and selected["effort"] not in cap.get("temperature_compatible_efforts",[]):
                raise PipelineError("Temperature plus explicit reasoning not verified; omit temperature")
            if mode=="budget":
                raise PipelineError("Temperature plus reasoning budget not verified")
            temperature=float(value)
        prompt=_text(stage.get("prompt_version"),"prompt_version")
        if prompt not in ALLOWED_PROMPT_VERSIONS[role]:
            raise PipelineError("Unregistered prompt version for role "+role)
        normalized.append({
          "role":role,"enabled":True,"model_id":model_id,
          "catalog_version_id":catalog_id,"reasoning":selected,
          "temperature":temperature,"max_output_tokens":max_output,
          "prompt_version":prompt})
        snapshots[role]={
          "catalog_version_id":catalog_id,"model_id":model_id,
          "capabilities":cap,"pricing":entry.get("pricing",{}),
          "verified_at":entry.get("verified_at"),"catalog_sha256":entry.get("catalog_sha256")}
    if seen[:2]!=list(REQUIRED) or (len(seen)==3 and seen[-1]!="taxonomy_critic"):
        raise PipelineError("Required order: facts -> human review -> taxonomy -> optional critic")
    return {"schema_version":1,"stages":normalized},snapshots

def serialize_openrouter_stage(stage: dict, snapshot: dict, *, json_schema: dict | None = None) -> dict:
    """Construct an adapter payload WITHOUT performing any request.

    Structured-output models require the actual schema before an executable
    request can be assembled; the Builder schemas arrive in Block 6.
    """
    cap=snapshot["capabilities"]
    request={"model":stage["model_id"],"max_tokens":stage["max_output_tokens"]}
    if stage["reasoning"]["mode"]=="effort":
        request["reasoning"]={"effort":stage["reasoning"]["effort"]}
    elif stage["reasoning"]["mode"]=="budget":
        request["reasoning"]={"max_tokens":stage["reasoning"]["budget_tokens"]}
    if stage["temperature"] is not None:
        request["temperature"]=stage["temperature"]
    if "structured_outputs" in cap["supported_parameters"]:
        if not isinstance(json_schema,dict) or not json_schema:
            raise PipelineError("The actual JSON schema is required for this stage")
        request["response_format"]={"type":"json_schema","json_schema":json_schema}
    elif "response_format" in cap["supported_parameters"]:
        request["response_format"]={"type":"json_object"}
    return request
