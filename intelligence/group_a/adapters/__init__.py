"""Adapters live at the boundary, never in Group A pure domain code.

GA-MOD-02 supplies an explicit unavailable adapter set, not counterfeit
successful I/O. Concrete QDP/asset/database/PSI adapters come in later stages.
"""
from .unavailable import disconnected_ports
from .qdp import PostgresQDPReader, CallbackQDPReader

__all__ = ["disconnected_ports", "PostgresQDPReader", "CallbackQDPReader"]
