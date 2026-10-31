"""Explicit illustrative policy selection, bound to the checked query identity."""
from datetime import date, timedelta
from pathlib import Path

from dmn import DecisionModel
from .decisions import DecisionTask


class RevenueReview:
    def __init__(self):
        self.model = DecisionModel.load(Path(__file__).resolve().parents[1] / "dmn/examples/revenue_review.json")

    def evaluate(self, adapter, *, receipt, audit=None):
        cases = receipt["plan"]["cases"]
        first = cases[0]
        if (any(c["company"] != first["company"] or c["metric"] != "revenue"
                or c["period"]["fiscal_year"] != first["period"]["fiscal_year"] for c in cases)
                or (len(cases) == 1 and first["period"]["kind"] != "fiscal_year")):
            return {**self.model.envelope(), "escalate_reason": "policy_scope_mismatch",
                    "reason": "This illustrative policy requires an annual revenue query.",
                    "evidence": [], "provenance": {}}
        ends = [f["valid_to"] for f in receipt["evidence"]]
        if not ends or not all(ends):
            return {**self.model.envelope(), "escalate_reason": "policy_date_unavailable",
                    "evidence": [], "provenance": {}}
        at = (date.fromisoformat(max(ends)) - timedelta(days=1)).isoformat()
        annual = {**first, "period": {"kind": "fiscal_year", "fiscal_year": first["period"]["fiscal_year"]}}
        return DecisionTask(self.model, at, {"revenue": {"fact": annual}}).evaluate(
            adapter, receipt=receipt, audit=audit)
