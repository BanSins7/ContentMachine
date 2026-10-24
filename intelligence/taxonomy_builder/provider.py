"""Single-request OpenRouter adapter for Block 6; never retries paid requests.

Network errors may have billed the provider. Treat missing usage/cost as an
UNKNOWN spend and do not release a reservation automatically.
"""
from __future__ import annotations
import json,os
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from decimal import Decimal
from .pipeline_contracts import PipelineError,serialize_openrouter_stage
from .run_contracts import prompt_for,schema_for
from .cost_estimator import rates,amount

URL="https://openrouter.ai/api/v1/chat/completions"
MAX_RESPONSE_BYTES=2_000_000

class ProviderUncertain(PipelineError):
    def __init__(self,message):
        super().__init__(message,502)

def request_payload(stage,snapshot,inputs):
    if stage["role"] in ("deep_first","deep_second","deep_expand"):
        from .deep_contracts import schema_for_deep,prompt_for_deep
        schema=schema_for_deep(stage["role"])
        base=serialize_openrouter_stage(stage,snapshot,json_schema=schema)
        base["messages"]=prompt_for_deep(stage["role"],inputs)
        base["provider"]={"require_parameters":True,"allow_fallbacks":False}
        base["stream"]=False
        base["usage"]={"include":True}
        return base
    # The exact Google Argon taxonomy schema proved susceptible to a provider
    # HTTP 400 INVALID_ARGUMENT. Use a deliberately compact JSON transport
    # shape; all grounded taxonomy constraints remain enforced server-side.
    # Keep facts and critic schemas, model choice and reasoning unchanged.
    google_argon_compat=(stage["role"]=="taxonomy_generation"
                          and stage["model_id"]=="google/gemini-4-argon")
    schema=schema_for(stage["role"],google_argon_compat=google_argon_compat,
                      context_mode=(stage["role"]=="fact_extraction" and
                                    stage.get("prompt_version")=="facts-context-v1"))
    base=serialize_openrouter_stage(stage,snapshot,json_schema=schema)
    base["messages"]=prompt_for(stage["role"],inputs,stage.get("prompt_version"))
    base["provider"]={"require_parameters":True,"allow_fallbacks":False}
    base["stream"]=False
    # OpenRouter currently sends usage by default. Keep the flag for older
    # routing paths; do NOT use a completion response without returned usage.
    base["usage"]={"include":True}
    return base

def reservation_cost(payload,snapshot):
    """Conservative pre-flight *reservation*, not a guaranteed provider ceiling.

    Byte length bounds ordinary UTF-8 token count with substantial buffer
    for the chat wrapper; reasoning output is bounded by max_tokens.
    """
    price=rates(snapshot["pricing"])
    serialized=json.dumps(payload["messages"],ensure_ascii=False)
    input_upper=len(serialized.encode("utf-8"))*2+2048
    max_tokens=payload["max_tokens"]
    cap=snapshot["capabilities"]
    if input_upper+max_tokens>int(cap.get("context_length") or 0):
        raise PipelineError("Request exceeds conservative model context window",422)
    input_charge=Decimal(input_upper)*price["prompt"]
    output_charge=Decimal(max_tokens)*(price["completion"]+price["internal_reasoning"])
    required=(input_charge+output_charge+price["request"])*Decimal("1.20")
    # Stored with 8 decimal places, rounding UP.
    quantum=Decimal("0.000000000001")
    return max(required.quantize(quantum,rounding="ROUND_CEILING"),quantum)

def safe_openrouter_http_error(exc,secret=""):
    """Return only provider error codes/message, never raw headers or user_id.

    OpenRouter error metadata can contain request/user details. The product
    displays only narrow allowlisted diagnostic fields, and still treats costs
    without usage as UNKNOWN. No retry even for HTTP 400.
    """
    try:
        raw=exc.read(12000)
        obj=json.loads(raw.decode("utf-8","replace"))
    except (ValueError,TypeError,UnicodeError,AttributeError,OSError):
        obj={}
    error=obj.get("error") if isinstance(obj,dict) else {}
    if not isinstance(error,dict):error={}
    meta=error.get("metadata") or {}
    if not isinstance(meta,dict):meta={}
    provider=str(meta.get("provider_name") or "provider")[:55]
    provider_code=str(meta.get("provider_error_code") or error.get("code") or "")[:40]
    message=str(error.get("message") or "")[:240]
    nested=meta.get("raw")
    reason=""
    if isinstance(nested,str) and len(nested)<=12000:
        try:
            inner=json.loads(nested)
            sub=inner.get("error") if isinstance(inner,dict) else None
            if isinstance(sub,dict):
                reason=str(sub.get("status") or sub.get("message") or "")[:100]
                detailed=str(sub.get("message") or "")[:170]
                if detailed and detailed!=reason:reason+=": "+detailed
        except ValueError:
            pass
    safe=(f"OpenRouter HTTP {int(exc.code)} ({provider} {provider_code})"+
          (": "+message if message else "")+
          ("; original provider error: "+reason if reason else "")+
          ". Billing not confirmed; no automatic retry.")
    return safe.replace(secret,"[REDACTED]") if secret else safe


def resolve_key():
    token=os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTERKEY")
    if token:return token
    # Reuse the existing private key saved by ContentMachine Settings.
    # Never print, return to browser, or save the key in an attempt record.
    from pathlib import Path
    from worker.openrouter_transcribe import get_key
    return get_key(Path(__file__).resolve().parents[2] / "worker")

def invoke(stage,snapshot,inputs,*,sender=None,key=None,timeout=55):
    token=key or (resolve_key() if sender is None else "mock-provider-fixture")
    if not token:
        raise PipelineError("OPENROUTER_API_KEY is not configured on the server",503)
    payload=request_payload(stage,snapshot,inputs)
    if sender is None:
        def sender(body):
            data=json.dumps(body,ensure_ascii=False).encode("utf-8")
            req=Request(URL,data=data,method="POST",headers={
                "Authorization":"Bearer "+token,
                "Content-Type":"application/json",
                "Accept":"application/json",
                "HTTP-Referer":"https://github.com/BanSins7/ContentMachine",
                "X-Title":"ContentMachine Taxonomy Builder"
            })
            try:
                with urlopen(req,timeout=timeout) as reply:
                    data=reply.read(MAX_RESPONSE_BYTES+1)
                    if len(data)>MAX_RESPONSE_BYTES:raise ProviderUncertain("Provider response too large")
                    return json.loads(data)
            except HTTPError as exc:
                raise ProviderUncertain(safe_openrouter_http_error(exc,token)) from exc
            except (URLError,TimeoutError,OSError,ValueError) as exc:
                # Never include URLs, headers, request body or API keys.
                raise ProviderUncertain(
                    "Provider transport/response error: "+type(exc).__name__+
                    ". Billing not confirmed; no automatic retry.") from exc
    try:
        body=sender(payload)
    except PipelineError:raise
    except Exception as exc:
        raise ProviderUncertain("Provider request outcome uncertain; no automatic retry") from exc
    if not isinstance(body,dict):
        raise ProviderUncertain("Provider returned a malformed result with unknown spend")
    usage=body.get("usage")
    if not isinstance(usage,dict) or usage.get("cost") is None:
        raise ProviderUncertain("Provider cost is missing; preserve reservation and reconcile")
    try:
        charged=amount(usage["cost"],"provider charged cost")
        prompt_count=int(usage["prompt_tokens"])
        output_count=int(usage["completion_tokens"])
    except (PipelineError,KeyError,ValueError,TypeError) as exc:
        raise ProviderUncertain("Provider usage is incomplete; preserve reservation") from exc
    if prompt_count<0 or output_count<0:
        raise ProviderUncertain("Provider token usage is invalid")
    choices=body.get("choices")
    if not isinstance(choices,list) or not choices:
        raise ProviderUncertain("Provider returned usage but no completion")
    choice=choices[0]
    content=choice.get("message",{}).get("content") if isinstance(choice,dict) else None
    if isinstance(content,list):
        content="".join(str(part.get("text","")) for part in content if isinstance(part,dict))
    if not isinstance(content,str):
        content=""
    try:
        parsed=json.loads(content)
    except (ValueError,TypeError):
        parsed=None
    return {"model_id":body.get("model"),"response_id":str(body.get("id","")),
        "finish_reason":choice.get("finish_reason"),"raw_json":parsed,
        "charged_usd":charged,
        "usage":{"prompt_tokens":prompt_count,"completion_tokens":output_count,
                 "reasoning_tokens":(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                 "cost_usd":str(charged)}}
