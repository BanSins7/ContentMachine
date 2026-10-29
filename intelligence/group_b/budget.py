"""GB-MOD-10: deterministic worst-case USD quote, without paid execution.

Quoted token bounds require verified catalog and conservatively include complete
request framing. Multimodal image cost unit/endpoint is *not* inferred from
the generic OpenRouter catalog; unknown image/caching costs fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, InvalidOperation

from .configuration import GroupBConfig
from .contracts import canonical_hash
from .context.builder import ContextPacket
from .providers.protocols import ModelDescriptor, ModelCatalogSnapshot
from .providers.openrouter import probe_model
from .errors import GroupBError

SCHEMA = "group-b-budget-quote/v1"
CENTI_MICRO = Decimal("0.000000000001")
MAX_QUOTE_TTL = timedelta(minutes=15)


class BudgetDenied(GroupBError):
    def __init__(self, reason: str):
        self.reason=reason
        super().__init__("budget_denied","budget: "+reason)


def _deny(reason: str):
    raise BudgetDenied(reason)


def _usd(value, *, positive=False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value<0 or (positive and value==0):
        _deny("invalid_usd_amount")
    if value.as_tuple().exponent < -12:
        _deny("money_scale_unrepresentable")
    if value >= Decimal("100000000"):
        _deny("money_out_of_range")
    return value


def _money_up(value: Decimal) -> Decimal:
    try:
        return value.quantize(CENTI_MICRO, rounding=ROUND_CEILING)
    except InvalidOperation:
        _deny("unquotable_price")


@dataclass(frozen=True, slots=True)
class BudgetQuote:
    project_id: str
    run_id: str
    block: str
    model_id: str
    request_sha256: str
    evidence_sha256: str
    context_sha256: str
    catalog_sha256: str
    prompt_tokens_ceiling: int
    completion_tokens_ceiling: int
    quoted_usd: Decimal
    pricing_snapshot: dict
    quote_details: dict
    created_at: datetime
    expires_at: datetime
    quote_sha256: str

    def payload(self) -> dict:
        return {
            "schema_version":SCHEMA,"project_id":self.project_id,
            "run_id":self.run_id,"block":self.block,"model_id":self.model_id,
            "request_sha256":self.request_sha256,
            "evidence_sha256":self.evidence_sha256,
            "context_sha256":self.context_sha256,
            "catalog_sha256":self.catalog_sha256,
            "prompt_tokens_ceiling":self.prompt_tokens_ceiling,
            "completion_tokens_ceiling":self.completion_tokens_ceiling,
            "quoted_usd":format(self.quoted_usd,"f"),
            "pricing_snapshot":self.pricing_snapshot,
            "quote_details":self.quote_details,
            "created_at":self.created_at.isoformat(),
            "expires_at":self.expires_at.isoformat(),
        }

    def verify(self) -> bool:
        try:
            return canonical_hash(self.payload()) == self.quote_sha256
        except (TypeError,ValueError):
            return False


def quote_context(*, packet: ContextPacket, catalog: ModelCatalogSnapshot,
                  model: str, request_sha256: str, output_tokens: int,
                  prompt_tokens_ceiling: int, created_at: datetime,
                  config: GroupBConfig | None = None,
                  ttl: timedelta = timedelta(minutes=10)) -> BudgetQuote:
    """Conservative token-only quote, fail closed on unpriced visual inputs.

    prompt_tokens_ceiling must bound the entire serialized payload and trusted
    framing. No image input is quoted without an external verified per-endpoint
    token/image billing policy, to be implemented by a later execution stage.
    """
    if not isinstance(packet, ContextPacket) or not packet.verify_digest():
        _deny("unverified_context")
    if (type(request_sha256) is not str or len(request_sha256)!=64 or
        any(ch not in "0123456789abcdef" for ch in request_sha256)):
        _deny("request_digest_invalid")
    if (not isinstance(created_at,datetime) or created_at.tzinfo is None or
        created_at.utcoffset() is None or
        created_at.astimezone(timezone.utc)>datetime.now(timezone.utc)+timedelta(minutes=5)):
        _deny("quote_clock_invalid")
    if type(ttl) is not timedelta or not timedelta(0)<ttl<=MAX_QUOTE_TTL:
        _deny("quote_ttl_invalid")
    if type(prompt_tokens_ceiling) is not int or prompt_tokens_ceiling<=0:
        _deny("prompt_token_bound_unknown")
    # A context packet's byte length is only a planning heuristic; demand a
    # separately supplied, strictly higher bound for trusted instructions.
    if prompt_tokens_ceiling < packet.estimated_token_ceiling+2048:
        _deny("prompt_token_bound_insufficient")
    chosen=probe_model(catalog,model=model,packet=packet,output_tokens=output_tokens)
    if not chosen.can_prepare or chosen.descriptor is None:
        _deny("model_capability_unavailable")
    d=chosen.descriptor
    if prompt_tokens_ceiling+output_tokens > d.context_tokens:
        _deny("catalog_context_limit")
    if packet.payload.get("coverage",{}).get("selected_image_refs",0):
        _deny("multimodal_billing_unit_unknown")
    if packet.payload.get("entries") is None or packet.payload.get("delivery_state")!="not_sent":
        _deny("context_not_unsent")
    _usd(d.prompt_usd_per_token)
    _usd(d.completion_usd_per_token)
    _usd(d.request_usd)
    # Catalog 'request' unit is per request; input/output rates per token.
    # Image provider units/caching surcharge remain intentionally unpriced.
    quoted=_money_up(
        d.prompt_usd_per_token*prompt_tokens_ceiling+
        d.completion_usd_per_token*output_tokens+d.request_usd
    )
    _usd(quoted,positive=True)
    cfg=config if config is not None else GroupBConfig()
    if not isinstance(cfg,GroupBConfig):
        _deny("budget_config_invalid")
    # Quoting is allowed in disabled mode, but no caller can reserve via
    # configuration defaults; explicit approval/policy is needed separately.
    if cfg.enable_paid_execution and quoted>cfg.max_paid_attempt_usd:
        _deny("per_attempt_ceiling_exceeded")
    details={
        "schema_version":SCHEMA,"block":packet.payload.get("block"),
        "packet_sha256":packet.packet_sha256,
        "evidence_sha256":packet.payload.get("source_evidence_manifest_sha256"),
        "image_refs":0,"prompt_tokens_ceiling":prompt_tokens_ceiling,
        "completion_tokens_ceiling":output_tokens,
        "provider_execution":"not_authorized",
        "billing_confidence":"text_tokens_only_conservative_bound",
    }
    pricing={
        "model":model,"catalog_sha256":catalog.catalog_sha256,
        "prompt_usd_per_token":str(d.prompt_usd_per_token),
        "completion_usd_per_token":str(d.completion_usd_per_token),
        "request_usd":str(d.request_usd),
        "image_pricing_status":"unverified_disabled",
    }
    ctx=packet.payload
    if not all(type(ctx.get(x)) is str for x in
               ("project_id","b_run_id","source_evidence_manifest_sha256","block")):
        _deny("context_identity_missing")
    params=dict(
        project_id=ctx["project_id"],run_id=ctx["b_run_id"],
        block=ctx["block"],model_id=model,request_sha256=request_sha256,
        evidence_sha256=ctx["source_evidence_manifest_sha256"],
        context_sha256=packet.packet_sha256,
        catalog_sha256=catalog.catalog_sha256,
        prompt_tokens_ceiling=prompt_tokens_ceiling,
        completion_tokens_ceiling=output_tokens,
        quoted_usd=quoted,pricing_snapshot=pricing,quote_details=details,
        created_at=created_at.astimezone(timezone.utc),
        expires_at=(created_at+ttl).astimezone(timezone.utc),
    )
    unsigned=BudgetQuote(**params,quote_sha256="")
    return BudgetQuote(**params,quote_sha256=canonical_hash(unsigned.payload()))
