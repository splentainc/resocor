"""Deterministic structured results to English, with crisp output by default.

No domain adapters, data stores, network clients or model inference are loaded.
Reuse NLG() when rendering multiple inputs to share the SimpleNLG lexicon.
"""
from .detail import DetailRealiser
from .detail_plan import LEVELS
from .renderer import CompositionalRealiser

__version__ = "0.1.0"
__all__ = ["NLG", "render", "LEVELS", "DetailRealiser", "CompositionalRealiser"]


class NLG(DetailRealiser):
    """Public reusable renderer: render(bundle, detail='crisp')."""


def render(bundle, *, detail="crisp"):
    """Render one complete bundle; use NLG() for repeated calls."""
    return NLG().render(bundle, detail=detail)
