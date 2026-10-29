"""PIE-MOD-02 opt-in controls; these settings do not start runtime services."""
from dataclasses import dataclass
from decimal import Decimal
from .contracts import PIEBoundaryError


@dataclass(frozen=True, slots=True)
class PIEExecutionSettings:
    """Safe default is pure computation only, without background or paid work.

    Future execution modules must enforce spend reservation, explicit user
    approval, durable idempotency and current source rights independently.
    """
    enable_workers: bool = False
    enable_paid_model: bool = False
    approved_spend_limit_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.enable_workers) is not bool or type(self.enable_paid_model) is not bool:
            raise PIEBoundaryError("invalid_settings", "Execution flags must be boolean")
        limit = self.approved_spend_limit_usd
        if limit is not None and (type(limit) is not Decimal or not limit.is_finite() or limit <= 0):
            raise PIEBoundaryError("invalid_settings", "Spend limit must be a positive finite Decimal")
        if self.enable_paid_model and limit is None:
            raise PIEBoundaryError("spend_approval_required", "Paid generation needs explicit spend limit")
