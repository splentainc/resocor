"""The facts a resolved period asks for: company, metric and period in, the stored facts out. No value is computed or supplied here.

period kinds: latest | fiscal_year | fiscal_quarter | calendar_date | range. Balance-sheet metrics (total_assets) are instants; a fiscal
year of any other metric reads the stored annual figure (`<metric>_annual`).
"""
from datetime import date

INSTANT_METRICS = {"total_assets"}
COMPANY_KEYS = {}  # the name a question resolves to -> the engine's company key


def facts_for(facts: dict, calendar: dict, case: dict) -> list:
    """case = {"company", "metric", "period": {...}}. Returns the matching Facts in date order, or [] when nothing is stored."""
    company = COMPANY_KEYS.get(case["company"], case["company"])
    metric, p = case["metric"], case["period"]
    instant = metric in INSTANT_METRICS
    predicate = metric + ("_annual" if p["kind"] == "fiscal_year" and not instant else "")
    found = sorted(facts.get((company, predicate), []), key=lambda f: f.valid_from)
    if p["kind"] == "latest":
        return found[-1:]
    if p["kind"] in ("fiscal_year", "fiscal_quarter"):
        key = ("FY", p["fiscal_year"]) if p["kind"] == "fiscal_year" else ("Q", p["fiscal_year"], p["quarter"])
        span = calendar[company].get(key)
        if not span:
            return []
        lo, hi = date.fromisoformat(span[0]), date.fromisoformat(span[1])
        if instant:
            return [f for f in found if f.valid_from == hi]
        return [f for f in found if f.valid_from == lo and (f.valid_to is None or f.valid_to > hi)]
    if p["kind"] == "calendar_date":
        d = date.fromisoformat(p["date"])
        return [f for f in found if f.valid_at(d)]
    if p["kind"] == "range":
        lo, hi = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
        return [f for f in found if f.valid_from <= hi and (f.valid_to is None or f.valid_to > lo)]
    return []
