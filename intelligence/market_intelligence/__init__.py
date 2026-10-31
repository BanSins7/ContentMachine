"""Market Intelligence: project-scoped, evidence-first pre-RTB research.

This package reuses Project Sources, Discovery D1 and verified Group B/C facts.
No provider calls, artificial virality scores, or implicit SRE/QDP bypass.
"""
from .contracts import MarketError, validate_seeds, summarize_market
from .service import MarketIntelligenceService

__all__ = ("MarketError", "validate_seeds", "summarize_market", "MarketIntelligenceService")
