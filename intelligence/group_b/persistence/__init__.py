"""GB-MOD-22 atomic one-version SQL005 persistence and project-scoped readout."""
from .repository import (
    VERSION,AtomicBError,AtomicBResult,persist_group_b_bundle,
    read_group_b_result,
)

__all__=("VERSION","AtomicBError","AtomicBResult","persist_group_b_bundle",
         "read_group_b_result")
