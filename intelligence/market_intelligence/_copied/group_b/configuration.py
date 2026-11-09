"""Dependency-free, inert-by-default Group B configuration (GB-MOD-02)."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .errors import ExecutionNotApproved

@dataclass(frozen=True, slots=True)
class GroupBConfig:
    """Local limits only; no credentials, approval, or jobs. Stage 10 owns consent."""
    enable_paid_execution: bool = False
    max_paid_attempt_usd: Decimal = Decimal("0")
    max_context_bytes: int = 2_000_000
    max_media_refs: int = 80
    max_attempts: int = 1

    def __post_init__(self):
        if type(self.enable_paid_execution) is not bool:
            raise ValueError("enable_paid_execution must be bool")
        if type(self.max_paid_attempt_usd) is not Decimal:
            raise ValueError("max_paid_attempt_usd must be Decimal")
        if not self.max_paid_attempt_usd.is_finite() or self.max_paid_attempt_usd < 0:
            raise ValueError("max_paid_attempt_usd must be finite and nonnegative")
        for name in ("max_context_bytes", "max_media_refs", "max_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be positive integer")
        if not self.enable_paid_execution and self.max_paid_attempt_usd != 0:
            raise ValueError("Disabled paid execution must have a zero ceiling")

    def require_paid_approval(self) -> None:
        """Never approve a paid call without later stage 10 attestation."""
        raise ExecutionNotApproved()
