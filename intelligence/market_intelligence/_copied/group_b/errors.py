"""Typed Group B integration failures; safe to surface without upstream payloads."""
from __future__ import annotations

class GroupBError(RuntimeError):
    """Stable code and safe message; never store raw media/secrets in exceptions."""
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        if not isinstance(code, str) or not code:
            raise ValueError("Error code required")
        super().__init__(message)
        self.code = code
        self.retryable = retryable

class DependencyUnavailable(GroupBError):
    def __init__(self, name: str, reason: str = "not_connected"):
        super().__init__("dependency_unavailable", f"{name}: {reason}")
        self.dependency = name
        self.reason = reason

class DependencyContractMismatch(GroupBError):
    def __init__(self, name: str):
        super().__init__("dependency_contract_mismatch", f"{name}: incompatible contract")
        self.dependency = name

class DependencyProbeFailed(GroupBError):
    def __init__(self, name: str):
        super().__init__("dependency_probe_failed", f"{name}: capability probe failed", retryable=True)
        self.dependency = name

class ExecutionNotApproved(GroupBError):
    def __init__(self):
        super().__init__("execution_not_approved", "Paid model execution is not approved")
