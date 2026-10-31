"""Exercise existing lookup, FEEL and NLG modules with one synthetic record.

This structured-input example does not invoke the natural-language parser or
the finance reporting adapter. It operates entirely on the supplied synthetic example data.
"""
import argparse
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from dmn import evaluate_checked
from engine.fact_lookup import facts_for
from engine.fact_store import Fact
from nlg import LEVELS, NLG

DATA = Path(__file__).with_name("data.json")


def load_example():
    return json.loads(DATA.read_text(encoding="utf-8"))


def answer(year=2024, *, detail="crisp", data=None):
    data = load_example() if data is None else data
    company = data["company"]
    store = {}
    for raw in data["facts"]:
        fact = Fact(**{**raw, "valid_from": date.fromisoformat(raw["valid_from"]),
                       "valid_to": date.fromisoformat(raw["valid_to"]) if raw["valid_to"] else None})
        store.setdefault((fact.company, fact.predicate), []).append(fact)
    calendar = {company: {("FY", p["year"]): (p["start"], p["end"]) for p in data["calendar"]}}
    selected = facts_for(store, calendar, {"company": company, "metric": "revenue",
                                         "period": {"kind": "fiscal_year", "fiscal_year": year}})
    if len(selected) != 1:
        return {"outcome": "abstained", "answer": None, "synthetic": True,
                "reason_code": "missing_or_ambiguous_facts",
                "reason": "Exactly one supplied record is required for the requested fiscal period.",
                "selected_count": len(selected)}
    fact = selected[0]
    checked = evaluate_checked("revenue > threshold", {
        "revenue": Decimal(fact.value), "threshold": Decimal(data["threshold"])})
    if checked.errors or type(checked.value) is not bool:
        return {"outcome": "abstained", "answer": None, "synthetic": True,
                "reason_code": "comparison_failed"}
    source_record = {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(fact).items()}
    scope = f"FY{year}"
    bundle = {
        "domain": {
            "name": "synthetic_example", "provenance": data["provenance"],
            "entities": {company: company},
            "metrics": {"revenue": {"noun": {"head": "revenue"}, "kind": "number", "unit": "USD"}},
            "units": {"USD": {"dimension": {"currency:USD": 1}, "prefix": "$"}},
            "scopes": {scope: {"label": f"fiscal year {year}", "preposition": "in"}}},
        "facts": [{"id": fact.fact_id, "entity": company, "metric": "revenue", "scope": scope,
                   "value": fact.value, "source": fact.source, "source_record": source_record}],
        "input": {"version": 2, "statement": {
            "node": "Relation", "op": "gt",
            "left": {"node": "Property", "entity": company, "metric": "revenue", "scope": scope},
            "right": {"node": "Quantity", "value": data["threshold"], "unit": "USD"}},
            "result": checked.value}}
    rendered = NLG().render(bundle, detail=detail)
    return {**rendered, "synthetic": True, "selected_fact_ids": [fact.fact_id],
            "comparison": {"expression": "revenue > threshold", "result": checked.value},
            "supplied_bundle": bundle}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--detail", choices=LEVELS, default="crisp")
    args = parser.parse_args()
    print(json.dumps(answer(args.year, detail=args.detail), indent=2))


if __name__ == "__main__":
    main()
