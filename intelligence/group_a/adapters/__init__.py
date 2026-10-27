"""Adapters live at the boundary, never in Group A pure domain code.

GA-MOD-02 supplies an explicit unavailable adapter set, not counterfeit
successful I/O. Concrete QDP/asset/database/PSI adapters come in later stages.
"""
from .unavailable import disconnected_ports
from .qdp import PostgresQDPReader, CallbackQDPReader
from .media_assets import VerifiedMediaCatalog
from .psi_reuse import PostgresPSIReader, PSIAssetBinding, PSIReuseCache, PSIReuseError

__all__ = ["disconnected_ports", "PostgresQDPReader", "CallbackQDPReader", "VerifiedMediaCatalog", "PostgresPSIReader",
           "PSIAssetBinding", "PSIReuseCache", "PSIReuseError"]

# A003 slide adapter is imported explicitly after extractor loading to avoid cycles.
