"""Group B standalone module bootstrap.

Only validated, dependency-free boundary types are implemented at GB-MOD-00.
No model invocation, QDP consumer, analysis engine, database writes, or automatic
registration is provided until the corresponding GB-MOD stages are complete.
"""
from .contracts import (
    ContractError,
    GroupBInputPointer,
    canonical_hash,
    content_type_for_kind,
    )
__all__ = ["ContractError", "GroupBInputPointer", "canonical_hash", "content_type_for_kind"]
